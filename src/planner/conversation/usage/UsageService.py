"""T3 UsageService.ts translated from revision 75e4ceb96489.

Copyright (c) 2026 T3 Tools Inc. MIT. See THIRD_PARTY_NOTICES.md.
Effect dependencies become injected Python dependencies. Byte scans run in threads.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import socket
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts.usage import USAGE_CONTRACT_VERSION, UsageReadError
from .usageAggregation import UsageAggregator, isoTimestamp
from .usagePricing import RateTable, createOverrideRateTable, parseRateTable
from .usageScanCache import decodeScanCache, dedupeWithinFile, encodeScanCache, pruneScanCache
from .usageTranscriptReader import listTranscriptFiles, readDirectoryVolumeId, readTranscriptRecords
from .usageTranscripts import finiteNumber, parseTimestampMs

LITELLM_RATES_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
)
RATES_TTL_MS = 24 * 60 * 60 * 1000
RATES_REFRESH_FLOOR_MS = 60 * 1000
MTIME_SLACK_MS = 36 * 60 * 60 * 1000
MAX_HOURLY_WINDOW_MS = 24 * 60 * 60 * 1000
CACHE_RETENTION_DAYS = 90
EMPTY_PRICING = dict(status="unavailable", source=LITELLM_RATES_URL, fetchedAt=None, knownModels=0)


def fetchRates() -> Any:
    with urllib.request.urlopen(LITELLM_RATES_URL, timeout=10) as response:
        return json.load(response)


class UsageService:
    def __init__(
        self,
        stateDir: Path | str,
        transcriptDirs: Any,
        *,
        ratesFetcher: Callable[[], Any] | None = None,
        settingsProvider: Callable[[], dict[str, Any]] | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self.stateDir = Path(stateDir)
        self.transcriptDirs = transcriptDirs
        self.ratesFetcher = ratesFetcher or fetchRates
        self.settingsProvider = settingsProvider or (lambda: {"usagePriceOverrides": {}})
        self.now = now or (lambda: time.time() * 1000)
        self.fileCache: dict[str, dict[str, Any]] = {}
        self.cacheDirty = False
        self.ratesCachePath = self.stateDir / "usage-model-rates.json"
        self.scanCachePath = self.stateDir / "usage-scan-cache.json"
        self.rates: RateTable = {}
        self.ratesFetchedAtMs: float | None = None
        self.ratesStatus = "unavailable"
        self.ratesLock = asyncio.Lock()
        self.cacheLock = threading.RLock()
        self.scanCacheLoaded = False
        self.inflightScans: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def pricing(self) -> dict[str, Any]:
        return dict(
            status=self.ratesStatus,
            source=LITELLM_RATES_URL,
            fetchedAt=None
            if self.ratesFetchedAtMs is None
            else isoTimestamp(self.ratesFetchedAtMs),
            knownModels=len(self.rates),
        )

    async def loadRates(self, force: bool) -> None:
        now = self.now()
        maxAgeMs = RATES_REFRESH_FLOOR_MS if force else RATES_TTL_MS
        if self.ratesFetchedAtMs is not None and now - self.ratesFetchedAtMs < maxAgeMs:
            return
        if self.ratesFetchedAtMs is None:
            try:
                fromDisk = json.loads(await asyncio.to_thread(self.ratesCachePath.read_text))
                if (
                    not isinstance(fromDisk, dict)
                    or not finiteNumber(fromDisk.get("fetchedAtMs"))
                    or "document" not in fromDisk
                ):
                    fromDisk = None
            except (OSError, ValueError):
                fromDisk = None
            if fromDisk is not None:
                parsed = parseRateTable(fromDisk["document"])
                if parsed:
                    self.rates = parsed
                    self.ratesFetchedAtMs = fromDisk["fetchedAtMs"]
                    self.ratesStatus = "cached"
                    if now - fromDisk["fetchedAtMs"] < maxAgeMs:
                        return
        try:
            if inspect.iscoroutinefunction(self.ratesFetcher):
                fetched = await asyncio.wait_for(self.ratesFetcher(), timeout=10)
            else:
                fetched = await asyncio.wait_for(asyncio.to_thread(self.ratesFetcher), timeout=10)
                if inspect.isawaitable(fetched):
                    fetched = await asyncio.wait_for(fetched, timeout=10)
        except Exception:
            fetched = None
        if fetched is None:
            if self.rates:
                self.ratesStatus = "cached"
            return
        parsed = parseRateTable(fetched)
        if not parsed:
            return
        self.rates = parsed
        self.ratesFetchedAtMs = now
        self.ratesStatus = "fresh"
        try:
            serialized = json.dumps(dict(fetchedAtMs=now, document=fetched), separators=(",", ":"))
            await asyncio.to_thread(self.ratesCachePath.write_text, serialized)
        except (OSError, ValueError):
            pass

    async def ensureRates(self, force: bool) -> None:
        async with self.ratesLock:
            await self.loadRates(force)

    async def refreshRates(self) -> dict[str, Any]:
        await self.ensureRates(True)
        return self.pricing()

    def ensureScanCacheLoaded(self) -> None:
        with self.cacheLock:
            if self.scanCacheLoaded:
                return
            try:
                document = json.loads(self.scanCachePath.read_text())
            except (OSError, ValueError):
                document = None
            if document is not None:
                self.fileCache.update(decodeScanCache(document))
            self.scanCacheLoaded = True

    def persistScanCache(self) -> None:
        with self.cacheLock:
            if not self.cacheDirty:
                return
            try:
                serialized = json.dumps(encodeScanCache(self.fileCache), separators=(",", ":"))
                self.scanCachePath.write_text(serialized)
                self.cacheDirty = False
            except (OSError, ValueError):
                pass

    def readFileRecords(
        self, filePath: str, size: int, mtimeMs: float, provider: str
    ) -> list[dict[str, Any]]:
        with self.cacheLock:
            cached = self.fileCache.get(filePath)
        if (
            cached
            and cached["size"] == size
            and cached["mtimeMs"] == mtimeMs
            and cached["provider"] == provider
        ):
            return (
                cached["records"]
                if not cached["tailRecords"]
                else [*cached["records"], *cached["tailRecords"]]
            )
        resumeFrom = (
            cached["position"]
            if cached is not None and cached["provider"] == provider and size > cached["size"]
            else None
        )
        parsed = readTranscriptRecords(filePath, provider, resumeFrom)
        if parsed is None:
            return []
        base = cached["records"] if parsed["resumed"] and cached is not None else []
        seen: set[str] = set()
        records = dedupeWithinFile([*base, *parsed["records"]], seen)
        tailRecords = dedupeWithinFile(parsed["tailRecords"], seen)
        with self.cacheLock:
            self.fileCache[filePath] = dict(
                size=size,
                mtimeMs=mtimeMs,
                provider=provider,
                records=records,
                tailRecords=tailRecords,
                position=parsed["position"],
            )
            self.cacheDirty = True
        return records if not tailRecords else [*records, *tailRecords]

    def collectDirs(self, windowStartMs: float, settings: dict[str, Any]) -> list[dict[str, Any]]:
        rawDirs = (
            self.transcriptDirs(settings) if callable(self.transcriptDirs) else self.transcriptDirs
        )
        dirs = [
            dict(provider=entry[0], dir=str(entry[1])) if isinstance(entry, tuple) else dict(entry)
            for entry in rawDirs
        ]
        scanned = []
        for source in dirs:
            provider, directory = source["provider"], source["dir"]
            volumeId = readDirectoryVolumeId(directory)
            if not Path(directory).exists():
                scanned.append(
                    dict(provider=provider, dir=directory, volumeId=volumeId, files=None)
                )
                continue
            options = {"fileName": source["fileName"]} if "fileName" in source else None
            files = listTranscriptFiles(directory, windowStartMs, options)
            parsedFiles = []
            for file in files:
                records = self.readFileRecords(
                    file["path"], file["size"], file["mtimeMs"], provider
                )
                parsedFiles.append(dict(path=file["path"], records=records))
            scanned.append(
                dict(provider=provider, dir=directory, volumeId=volumeId, files=parsedFiles)
            )
        return scanned

    async def scanSummary(self, input: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
        if input["sinceDay"] > input["untilDay"]:
            raise UsageReadError(
                "invalidWindow",
                f"sinceDay '{input['sinceDay']}' is after untilDay '{input['untilDay']}'",
            )
        hourlyWindow: dict[str, Any] = {}
        if input.get("resolution") == "hour":
            sinceTimeMs = parseTimestampMs(input.get("sinceTime"))
            untilTimeMs = parseTimestampMs(input.get("untilTime"))
            if sinceTimeMs is None or untilTimeMs is None:
                raise UsageReadError(
                    "invalidWindow", "Hourly usage requires valid sinceTime and untilTime instants"
                )
            durationMs = untilTimeMs - sinceTimeMs
            if durationMs <= 0 or durationMs > MAX_HOURLY_WINDOW_MS:
                raise UsageReadError(
                    "invalidWindow",
                    "Hourly usage window must be greater than zero and at most 24 hours",
                )
            hourlyWindow = dict(sinceTimeMs=sinceTimeMs, untilTimeMs=untilTimeMs)
        startedAtMs = self.now()
        await asyncio.to_thread(self.ensureScanCacheLoaded)
        hostId = socket.gethostname()
        windowStart = parseTimestampMs(f"{input['sinceDay']}T00:00:00Z")
        if windowStart is None:
            raise UsageReadError(
                "invalidWindow", f"sinceDay '{input['sinceDay']}' is not a valid date"
            )
        windowStartMs = hourlyWindow.get("sinceTimeMs", windowStart) - MTIME_SLACK_MS
        _, scannedDirs = await asyncio.gather(
            self.ensureRates(False), asyncio.to_thread(self.collectDirs, windowStartMs, settings)
        )
        aggregator = UsageAggregator(
            dict(
                timeZone=input["timeZone"],
                sinceDay=input["sinceDay"],
                untilDay=input["untilDay"],
                resolution=input.get("resolution", "day"),
                **hourlyWindow,
                rates=self.rates,
                priceOverrides=createOverrideRateTable(settings.get("usagePriceOverrides", {})),
            )
        )
        sources = []
        livePaths: set[str] = set()
        walkedRoots = []
        for scanned in scannedDirs:
            provider, directory, volumeId, files = (
                scanned[key] for key in ("provider", "dir", "volumeId", "files")
            )
            fingerprint = dict(
                hostId=hostId, provider=provider, resolvedHomePath=directory, volumeId=volumeId
            )
            if files is None:
                sources.append(
                    dict(
                        fingerprint=fingerprint,
                        status="missing",
                        scannedFiles=0,
                        skippedFiles=0,
                        malformedRecords=0,
                        distinctSessions=0,
                        message="No transcript directory on this environment.",
                    )
                )
                continue
            walkedRoots.append(directory)
            scannedFiles = 0
            skippedFiles = 0
            sessionIds: set[str] = set()
            for file in files:
                livePaths.add(file["path"])
                if not file["records"]:
                    skippedFiles += 1
                    continue
                scannedFiles += 1
                for record in file["records"]:
                    if aggregator.add(record) and record["sessionId"]:
                        sessionIds.add(record["sessionId"])
            sources.append(
                dict(
                    fingerprint=fingerprint,
                    status="ok",
                    scannedFiles=scannedFiles,
                    skippedFiles=skippedFiles,
                    malformedRecords=0,
                    distinctSessions=len(sessionIds),
                    message=None,
                )
            )
        with self.cacheLock:
            pruned = pruneScanCache(
                self.fileCache,
                dict(
                    livePaths=livePaths,
                    walkedRoots=walkedRoots,
                    windowStartMs=windowStartMs,
                    retentionCutoffMs=startedAtMs - CACHE_RETENTION_DAYS * 24 * 60 * 60 * 1000,
                ),
            )
            if pruned > 0:
                self.cacheDirty = True
        await asyncio.to_thread(self.persistScanCache)
        aggregated = aggregator.finish()
        readAt = isoTimestamp(self.now())
        finishedAtMs = self.now()
        return dict(
            contractVersion=USAGE_CONTRACT_VERSION,
            readAt=readAt,
            timeZone=input["timeZone"],
            sinceDay=input["sinceDay"],
            untilDay=input["untilDay"],
            buckets=aggregated["buckets"],
            sources=sources,
            pricing=self.pricing(),
            scanDurationMs=max(0, int(finishedAtMs - startedAtMs)),
        )

    async def readSummary(self, input: dict[str, Any]) -> dict[str, Any]:
        try:
            settings = self.settingsProvider()
        except Exception as error:
            raise UsageReadError("scanFailed", "Server settings could not be read.") from error
        key = json.dumps(
            [
                input["timeZone"],
                input["sinceDay"],
                input["untilDay"],
                input.get("resolution", "day"),
                input.get("sinceTime"),
                input.get("untilTime"),
                settings.get("usagePriceOverrides", {}),
            ],
            separators=(",", ":"),
        )
        task = self.inflightScans.get(key)
        if task is None:
            # No await separates enrollment from detached task creation.
            task = asyncio.create_task(self.scanSummary(input, settings))
            self.inflightScans[key] = task

            def finish(finished: asyncio.Task[dict[str, Any]]) -> None:
                self.inflightScans.pop(key, None)
                if not finished.cancelled():
                    finished.exception()  # Observe detached failures even when all callers leave.

            task.add_done_callback(finish)
        result = await asyncio.shield(task)
        return result
