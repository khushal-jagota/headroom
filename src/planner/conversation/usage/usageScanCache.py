"""T3 usageScanCache.ts translated from revision 75e4ceb96489.

Copyright (c) 2026 T3 Tools Inc. MIT. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import os
from typing import Any

from .usageTranscriptReader import GUARD_LENGTH
from .usageTranscripts import finiteNumber

USAGE_SCAN_CACHE_VERSION = 5
ScanCache = dict[str, dict[str, Any]]
CachedFile = dict[str, Any]
INVALID = object()


def encodeScanCache(cache: ScanCache) -> dict[str, Any]:
    models: list[str] = []
    sessions: list[str] = []
    modelIndex: dict[str, int] = {}
    sessionIndex: dict[str, int] = {}

    def intern(table: list[str], index: dict[str, int], value: str) -> int:
        if value in index:
            return index[value]
        nextIndex = len(table)
        table.append(value)
        index[value] = nextIndex
        return nextIndex

    def serializeRecord(record: dict[str, Any]) -> list[Any]:
        totals = record["totals"]
        return [
            record["timestampMs"],
            intern(models, modelIndex, record["model"]),
            intern(sessions, sessionIndex, record["sessionId"]),
            totals["uncachedInputTokens"],
            totals["cachedInputTokens"],
            totals["cacheCreationTokens"],
            totals["outputTokens"],
            totals["reasoningTokens"],
            record["dedupeKey"],
            record["reportedCostUsd"],
            record.get("cacheCreation1hTokens", 0),
            {key: record[key] for key in ("serviceTier", "compactionCovered") if key in record},
        ]

    files: dict[str, Any] = {}
    for path, entry in cache.items():
        position = entry["position"]
        files[path] = dict(
            s=entry["size"],
            m=entry["mtimeMs"],
            p=entry["provider"],
            r=[serializeRecord(record) for record in entry["records"]],
            t=[serializeRecord(record) for record in entry["tailRecords"]],
            o=position["resumeOffset"],
            gl=position["guardLength"],
            gh=position["guardHash"],
            cs=position["codexState"],
        )
    return dict(version=USAGE_SCAN_CACHE_VERSION, models=models, sessions=sessions, files=files)


def decodeCodexState(value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, dict):
        return INVALID
    if (
        not isinstance(value.get("model"), str)
        or not isinstance(value.get("sessionId"), str)
        or (
            "lastUsageSignature" not in value
            or value["lastUsageSignature"] is not None
            and not isinstance(value["lastUsageSignature"], str)
        )
        or not isinstance(value.get("sawSessionMeta"), bool)
        or not isinstance(value.get("suppressingForkCopies"), bool)
        or not finiteNumber(value.get("forkCopyAnchorMs"))
    ):
        return INVALID
    if (
        "serviceTier" not in value
        or "cumulativeUsage" not in value
        or not isinstance(value.get("compactions"), dict)
        or not isinstance(value.get("compactionMarkers"), dict)
        or not isinstance(value.get("completedCompactions"), list)
    ):
        return INVALID

    def counters(item: Any) -> bool:
        return isinstance(item, dict) and all(
            isinstance(k, str) and safeInteger(v) and v >= 0 for k, v in item.items()
        )

    if (value["serviceTier"] is not None and not isinstance(value["serviceTier"], str)) or (
        value["cumulativeUsage"] is not None and not counters(value["cumulativeUsage"])
    ):
        return INVALID
    if not all(isinstance(item, str) for item in value["completedCompactions"]):
        return INVALID
    for identifier, snapshots in value["compactionMarkers"].items():
        if not isinstance(identifier, str) or not isinstance(snapshots, list):
            return INVALID
        for snapshot in snapshots:
            if (
                not isinstance(snapshot, dict)
                or not counters(snapshot.get("current"))
                or not counters(snapshot.get("last"))
                or "previous" not in snapshot
                or (snapshot["previous"] is not None and not counters(snapshot["previous"]))
            ):
                return INVALID
    for identifier, candidate in value["compactions"].items():
        if not isinstance(identifier, str) or not isinstance(candidate, dict):
            return INVALID
        record = candidate.get("record")
        if (
            not counters(candidate.get("usage"))
            or not counters(candidate.get("cumulative"))
            or not isinstance(candidate.get("covered"), bool)
            or not isinstance(record, dict)
        ):
            return INVALID
        if (
            record.get("provider") != "codex"
            or not isinstance(record.get("model"), str)
            or not isinstance(record.get("sessionId"), str)
            or not finiteNumber(record.get("timestampMs"))
            or not isinstance(record.get("dedupeKey"), str)
            or not counters(record.get("totals"))
            or not all(
                key in record["totals"]
                for key in (
                    "uncachedInputTokens",
                    "cachedInputTokens",
                    "cacheCreationTokens",
                    "outputTokens",
                    "reasoningTokens",
                )
            )
            or "reportedCostUsd" not in record
        ):
            return INVALID
    return {
        key: value[key]
        for key in (
            "model",
            "sessionId",
            "lastUsageSignature",
            "sawSessionMeta",
            "suppressingForkCopies",
            "forkCopyAnchorMs",
            "serviceTier",
            "compactions",
            "compactionMarkers",
            "completedCompactions",
            "cumulativeUsage",
        )
    }


def safeInteger(value: Any) -> bool:
    return finiteNumber(value) and value == int(value) and abs(value) <= 2**53 - 1


def decodeScanCache(document: Any) -> ScanCache:
    cache: ScanCache = {}
    if not isinstance(document, dict) or document.get("version") != USAGE_SCAN_CACHE_VERSION:
        return cache
    models, sessions, files = (
        document.get("models"),
        document.get("sessions"),
        document.get("files"),
    )
    if (
        not isinstance(models, list)
        or not isinstance(sessions, list)
        or not isinstance(files, dict)
    ):
        return cache
    if not all(isinstance(value, str) for value in models) or not all(
        isinstance(value, str) for value in sessions
    ):
        return cache

    def tableValue(table: list[str], index: Any) -> str | None:
        if not safeInteger(index) or index < 0 or index >= len(table):
            return None
        return table[int(index)]

    def decodeRecords(rows: list[Any], provider: str) -> list[dict[str, Any]] | None:
        records = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 11:
                return None
            (
                timestampMs,
                modelIndex,
                sessionIndex,
                uncached,
                cached,
                cacheCreation,
                output,
                reasoning,
                dedupeKey,
                reportedCostUsd,
                cacheCreation1hTokens,
            ) = row[:11]
            model = tableValue(models, modelIndex)
            if (
                not safeInteger(cacheCreation1hTokens)
                or cacheCreation1hTokens < 0
                or not finiteNumber(timestampMs)
                or model is None
                or not all(
                    finiteNumber(value)
                    for value in (uncached, cached, cacheCreation, output, reasoning)
                )
                or cacheCreation1hTokens > cacheCreation
            ):
                return None
            metadata = row[11] if len(row) > 11 else {}
            if (
                not isinstance(metadata, dict)
                or any(key not in ("serviceTier", "compactionCovered") for key in metadata)
                or (
                    "serviceTier" in metadata
                    and metadata["serviceTier"] is not None
                    and not isinstance(metadata["serviceTier"], str)
                )
                or (
                    "compactionCovered" in metadata
                    and not isinstance(metadata["compactionCovered"], bool)
                )
            ):
                return None
            records.append(
                dict(
                    **(
                        {"cacheCreation1hTokens": cacheCreation1hTokens}
                        if cacheCreation1hTokens
                        else {}
                    ),
                    **metadata,
                    provider=provider,
                    timestampMs=timestampMs,
                    model=model,
                    sessionId=tableValue(sessions, sessionIndex) or "",
                    totals=dict(
                        uncachedInputTokens=uncached,
                        cachedInputTokens=cached,
                        cacheCreationTokens=cacheCreation,
                        outputTokens=output,
                        reasoningTokens=reasoning,
                    ),
                    reportedCostUsd=reportedCostUsd
                    if isinstance(reportedCostUsd, (int, float))
                    and not isinstance(reportedCostUsd, bool)
                    else None,
                    dedupeKey=dedupeKey if isinstance(dedupeKey, str) else None,
                )
            )
        return records

    for path, entry in files.items():
        if not isinstance(entry, dict):
            continue
        if (
            not isinstance(entry.get("s"), (int, float))
            or isinstance(entry.get("s"), bool)
            or not isinstance(entry.get("m"), (int, float))
            or isinstance(entry.get("m"), bool)
            or entry.get("p") not in ("claude", "codex", "grok")
            or not isinstance(entry.get("r"), list)
            or not isinstance(entry.get("t"), list)
        ):
            continue
        if (
            not safeInteger(entry.get("o"))
            or entry["o"] < 0
            or not safeInteger(entry.get("gl"))
            or entry["gl"] < 0
            or entry["gl"] > GUARD_LENGTH
            or entry["gl"] > entry["o"]
            or not finiteNumber(entry.get("gh"))
        ):
            continue
        codexState = decodeCodexState(entry.get("cs", INVALID))
        if codexState is INVALID:
            continue
        records = decodeRecords(entry["r"], entry["p"])
        tailRecords = decodeRecords(entry["t"], entry["p"])
        if records is None or tailRecords is None:
            continue
        cache[path] = dict(
            size=entry["s"],
            mtimeMs=entry["m"],
            provider=entry["p"],
            records=records,
            tailRecords=tailRecords,
            position=dict(
                resumeOffset=entry["o"],
                guardLength=entry["gl"],
                guardHash=entry["gh"],
                codexState=codexState,
            ),
        )
    return cache


def pruneScanCache(cache: ScanCache, options: dict[str, Any]) -> int:
    removed = 0
    for path, entry in list(cache.items()):
        agedOut = entry["mtimeMs"] < options["retentionCutoffMs"]
        underWalkedRoot = False
        for root in options["walkedRoots"]:
            relative = os.path.relpath(path, root)
            if relative == "." or (
                relative != ".."
                and not relative.startswith(".." + os.sep)
                and not os.path.isabs(relative)
            ):
                underWalkedRoot = True
                break
        deleted = (
            underWalkedRoot
            and entry["mtimeMs"] >= options["windowStartMs"]
            and path not in options["livePaths"]
        )
        if agedOut or deleted:
            del cache[path]
            removed += 1
    return removed


def dedupeWithinFile(
    records: list[dict[str, Any]], seen: set[str] | None = None
) -> list[dict[str, Any]]:
    seen = set() if seen is None else seen
    kept = []
    for record in records:
        if record["dedupeKey"] is not None:
            if record["dedupeKey"] in seen:
                continue
            seen.add(record["dedupeKey"])
        kept.append(record)
    return kept
