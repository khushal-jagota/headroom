"""T3 usageTranscriptReader.ts translated from revision 75e4ceb96489.

Copyright (c) 2026 T3 Tools Inc. MIT. See THIRD_PARTY_NOTICES.md.
Node streams become synchronous byte streams, called outside the event loop.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from .usageTranscripts import (
    initialCodexScanState,
    mightCarryUsage,
    parseClaudeLine,
    parseCodexLine,
    parseGrokLine,
)

GUARD_LENGTH = 64
TranscriptFile = dict[str, Any]
TranscriptParsePosition = dict[str, Any]
TranscriptParseResult = dict[str, Any]


def fnv1a(buffer: bytes) -> int:
    value = 0x811C9DC5
    for byte in buffer:
        value = ((value ^ byte) * 0x01000193) & 0xFFFFFFFF
    return value


def listTranscriptFiles(
    root: str, sinceMs: float, options: dict[str, Any] | None = None
) -> list[TranscriptFile]:
    found: list[TranscriptFile] = []
    fileName = (options or {}).get("fileName")

    def walk(directory: str) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return
        for entry in entries:
            child = os.path.join(directory, entry.name)
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk(child)
                    continue
                if fileName is not None:
                    if entry.name != fileName:
                        continue
                elif not entry.name.endswith(".jsonl"):
                    continue
                stat = os.stat(child)
                if stat.st_mtime_ns / 1_000_000 >= sinceMs:
                    found.append(
                        dict(path=child, size=stat.st_size, mtimeMs=stat.st_mtime_ns / 1_000_000)
                    )
            except OSError:
                continue

    walk(root)
    return found


def readDirectoryVolumeId(path: str) -> str:
    try:
        stat = os.stat(path)
        return f"{stat.st_dev}:{stat.st_ino}"
    except OSError:
        return ""


def guardMatches(handle: BinaryIO, position: TranscriptParsePosition) -> bool:
    if position["guardLength"] <= 0 or position["guardLength"] > GUARD_LENGTH:
        return False
    try:
        handle.seek(position["resumeOffset"] - position["guardLength"])
        window = handle.read(position["guardLength"])
        return bool(
            len(window) == position["guardLength"] and fnv1a(window) == position["guardHash"]
        )
    except (OSError, ValueError):
        return False


def selectedAllowanceObservation(line: str, provisional: bool) -> dict[str, Any] | None:
    """Approved extension: emit only allowance metadata, never transcript bodies."""
    if '"rate_limits"' not in line and '"rateLimitInfo"' not in line:
        return None
    try:
        record = json.loads(line)
    except ValueError:
        return None
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        # Claude SDK rate_limit_event carries rateLimitInfo at the top level.
        payload = record if record.get("type") == "rate_limit_event" else {}
    selected: dict[str, Any] = {key: record[key] for key in ("timestamp", "type") if key in record}
    selected["provisional"] = provisional

    # Only the explicitly selected allowance shape crosses the adapter boundary.
    def window(value: Any) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {
            key: value[key]
            for key in (
                "used_percent",
                "window_minutes",
                "resets_at",
                "usedPercent",
                "windowDurationMins",
                "resetsAt",
                "utilization",
                "resetsAtMs",
            )
            if key in value
        }

    for field in ("rate_limits", "rateLimitInfo"):
        if field == "rate_limits" and payload.get("type") != "token_count":
            continue
        raw = payload.get(field)
        if not isinstance(raw, dict):
            continue
        limits = {
            key: raw[key]
            for key in (
                "limit_id",
                "limit_name",
                "limitId",
                "limitName",
                "plan_type",
                "rate_limit_reached_type",
                "rateLimitType",
                "utilization",
                "resetsAt",
            )
            if key in raw
        }
        for key in ("primary", "secondary", "five_hour", "seven_day", "fiveHour", "sevenDay"):
            if key in raw and raw[key] is None:
                limits[key] = None
                continue
            value = window(raw.get(key))
            if value is not None:
                limits[key] = value
        selected[field] = limits
    return selected if "rate_limits" in selected or "rateLimitInfo" in selected else None


def readTranscriptRecords(
    filePath: str,
    provider: str,
    resumeFrom: TranscriptParsePosition | None = None,
    *,
    observationCallback: Callable[[dict[str, Any]], None] | None = None,
    resumeCallback: Callable[[bool], None] | None = None,
) -> TranscriptParseResult | None:
    try:
        handle = Path(filePath).open("rb")
    except OSError:
        return None
    try:
        codexState = initialCodexScanState()
        resumed = False
        start = 0
        if (
            resumeFrom is not None
            and resumeFrom["resumeOffset"] > 0
            and (provider != "codex" or resumeFrom["codexState"] is not None)
            and guardMatches(handle, resumeFrom)
        ):
            if resumeFrom["codexState"] is not None:
                codexState = dict(resumeFrom["codexState"])
            start = resumeFrom["resumeOffset"]
            resumed = True
        if resumeCallback is not None:
            resumeCallback(resumed)

        def parseLine(
            line: str, state: dict[str, Any], out: list[dict[str, Any]], provisional: bool = False
        ) -> None:
            if observationCallback is not None:
                observation = selectedAllowanceObservation(line, provisional)
                if observation is not None:
                    observationCallback(observation)
            if provider == "codex":
                if (
                    not mightCarryUsage(line, provider)
                    and '"turn_context"' not in line
                    and '"session_meta"' not in line
                ):
                    return
                record = parseCodexLine(line, state)
                if record is not None:
                    out.append(record)
                return
            if not mightCarryUsage(line, provider):
                return
            if provider == "grok":
                out.extend(parseGrokLine(line))
                return
            record = parseClaudeLine(line)
            if record is not None:
                out.append(record)

        def toLineString(buffer: bytes) -> str:
            content = buffer[:-1] if buffer.endswith(b"\r") else buffer
            return content.decode("utf-8", errors="replace")

        records: list[dict[str, Any]] = []
        resumeOffset = start
        pendingChunks: list[bytes] = []
        handle.seek(start)
        while chunk := handle.read(64 * 1024):
            if b"\n" not in chunk:
                pendingChunks.append(chunk)
                continue
            buffer = chunk if not pendingChunks else b"".join([*pendingChunks, chunk])
            pendingChunks = []
            lineStart = 0
            while True:
                newlineIndex = buffer.find(b"\n", lineStart)
                if newlineIndex == -1:
                    break
                parseLine(toLineString(buffer[lineStart:newlineIndex]), codexState, records)
                lineStart = newlineIndex + 1
            resumeOffset += lineStart
            if lineStart < len(buffer):
                pendingChunks.append(buffer[lineStart:])
        tailRecords: list[dict[str, Any]] = []
        if pendingChunks:
            pending = pendingChunks[0] if len(pendingChunks) == 1 else b"".join(pendingChunks)
            if pending:
                parseLine(toLineString(pending), dict(codexState), tailRecords, True)
        guardLength = min(GUARD_LENGTH, resumeOffset)
        guardHash = 0
        if guardLength > 0:
            handle.seek(resumeOffset - guardLength)
            window = handle.read(guardLength)
            # Node Buffer.alloc is zero-filled when a concurrent truncation shortens the read.
            guardHash = fnv1a(window.ljust(guardLength, b"\0"))
        return dict(
            records=records,
            tailRecords=tailRecords,
            position=dict(
                resumeOffset=resumeOffset,
                guardLength=guardLength,
                guardHash=guardHash,
                codexState=codexState if provider == "codex" else None,
            ),
            resumed=resumed,
        )
    except Exception:
        return None
    finally:
        handle.close()
