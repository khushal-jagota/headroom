"""Measure isolated T3 service scans against read-only local history.

This verification script writes only its named worktree state and aggregates.
It does not invoke native refreshes, backends, providers, or authentication files.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from planner.conversation.usage.localUsageLimits import LocalUsageLimits
from planner.conversation.usage.UsageService import UsageService

ROOTS = [
    ("codex", Path.home() / ".codex" / "sessions"),
    ("claude", Path.home() / ".claude" / "projects"),
]


async def measure(state: Path, output: Path, phase: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    previous = json.loads(output.read_text()) if output.exists() else {}
    if "window" in previous:
        window = previous["window"]
    else:
        until = datetime.now(UTC).replace(second=0, microsecond=0)
        since = until - timedelta(hours=24)
        window = dict(
            sinceDay=since.date().isoformat(),
            untilDay=until.date().isoformat(),
            timeZone="Europe/Berlin",
            resolution="hour",
            sinceTime=since.isoformat().replace("+00:00", "Z"),
            untilTime=until.isoformat().replace("+00:00", "Z"),
        )
    serviceModule = importlib.import_module("planner.conversation.usage.UsageService")
    originalOpen = Path.open
    originalList = serviceModule.listTranscriptFiles
    originalRead = serviceModule.readTranscriptRecords
    metrics: dict[str, Any] = {}
    inventory: list[dict[str, Any]] = []

    class MeasuredFile:
        def __init__(self, wrapped: Any) -> None:
            self.wrapped = wrapped

        def read(self, *args: Any, **kwargs: Any) -> bytes:
            result: bytes = self.wrapped.read(*args, **kwargs)
            metrics["actualTranscriptBytesRead"] += len(result)
            return result

        def __getattr__(self, name: str) -> Any:
            return getattr(self.wrapped, name)

        def __enter__(self) -> MeasuredFile:
            return self

        def __exit__(self, *args: Any) -> None:
            self.wrapped.close()

    def measuredOpen(path: Path, *args: Any, **kwargs: Any) -> Any:
        file = originalOpen(path, *args, **kwargs)
        if path.suffix == ".jsonl" and any(path.is_relative_to(root) for _, root in ROOTS):
            metrics["transcriptOpens"] += 1
            return MeasuredFile(file)
        return file

    def measuredList(root: str, sinceMs: float, options: Any = None) -> Any:
        files = originalList(root, sinceMs, options)
        inventory.extend(files)
        return files

    def measuredRead(*args: Any, **kwargs: Any) -> Any:
        result = originalRead(*args, **kwargs)
        metrics["parsedFiles"] += 1
        if result is None:
            metrics["failedReads"] += 1
        elif result["resumed"]:
            metrics["resumedFiles"] += 1
        return result

    def construct() -> UsageService:
        return UsageService(state, ROOTS, localUsageLimits=LocalUsageLimits(state))

    async def scan(service: UsageService, label: str) -> dict[str, Any]:
        nonlocal inventory, metrics
        inventory = []
        metrics = dict(
            actualTranscriptBytesRead=0,
            transcriptOpens=0,
            parsedFiles=0,
            failedReads=0,
            resumedFiles=0,
        )
        started = time.perf_counter()
        result = await service.readSummary(window)
        metrics["elapsedMs"] = (time.perf_counter() - started) * 1000
        metrics["corpusFiles"] = len(inventory)
        metrics["corpusBytes"] = sum(item["size"] for item in inventory)
        metrics["completedFiles"] = len(inventory) - metrics["failedReads"]
        metrics["completedCorpusBytes"] = (
            metrics["corpusBytes"] if metrics["failedReads"] == 0 else None
        )
        metrics["unchangedCacheFiles"] = len(inventory) - metrics["parsedFiles"]
        # Cache-hit volume is a corpus measure, separate from physical reads.
        # After the scan, unchanged candidates equal the ones not passed to the reader.
        metrics["cacheHitBytes"] = sum(
            item["size"] for item in inventory if item["path"] not in parsedPaths
        )
        metrics["completedSources"] = result["sources"]
        metrics["recordCount"] = sum(bucket["records"] for bucket in result["buckets"])
        metrics["totalTokens"] = sum(
            sum(
                bucket["totals"][key]
                for key in [
                    "uncachedInputTokens",
                    "cachedInputTokens",
                    "cacheCreationTokens",
                    "outputTokens",
                ]
            )
            for bucket in result["buckets"]
        )
        metrics["costUsd"] = sum(bucket["costUsd"] for bucket in result["buckets"])
        metrics["limits"] = service.localUsageLimits.current()
        receipt = dict(
            label=label,
            metrics=metrics,
            summary=result,
            inventory=[
                dict(
                    identity=hashlib.sha256(item["path"].encode()).hexdigest(),
                    size=item["size"],
                    mtimeMs=item["mtimeMs"],
                )
                for item in inventory
            ],
        )
        print(json.dumps({"phase": label, **metrics}), flush=True)
        return receipt

    parsedPaths: set[str] = set()
    actualMeasuredRead = measuredRead

    def trackedRead(*args: Any, **kwargs: Any) -> Any:
        parsedPaths.add(args[0])
        return actualMeasuredRead(*args, **kwargs)

    Path.open = measuredOpen  # type: ignore[method-assign]
    serviceModule.listTranscriptFiles = measuredList
    serviceModule.readTranscriptRecords = trackedRead
    try:
        receipts = previous.get("scans", [])
        if phase == "initial":
            service = construct()
            receipts.append(await scan(service, "cold"))
            parsedPaths.clear()
            receipts.append(await scan(service, "warm"))
            parsedPaths.clear()
            receipts.append(await scan(construct(), "restart"))
        else:
            receipts.append(await scan(construct(), phase))
        output.write_text(json.dumps(dict(window=window, scans=receipts), indent=2) + "\n")
        auditInput = dict(
            referenceRoot="/home/vps/projects/t3code-baseline-75e4ceb",
            ratesCache=str(state / "usage-model-rates.json"),
            window=window,
            roots=[(provider, str(root)) for provider, root in ROOTS],
        )
        (output.parent / "reference-input.json").write_text(json.dumps(auditInput, indent=2) + "\n")
    finally:
        Path.open = originalOpen  # type: ignore[method-assign]
        serviceModule.listTranscriptFiles = originalList
        serviceModule.readTranscriptRecords = originalRead


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--state", type=Path, required=True)
    args.add_argument("--output", type=Path, required=True)
    args.add_argument("--phase", default="initial")
    parsed = args.parse_args()
    asyncio.run(measure(parsed.state.resolve(), parsed.output.resolve(), parsed.phase))
