"""T3 usageTranscriptReader.ts translated from revision 75e4ceb96489.

Copyright (c) 2026 T3 Tools Inc. MIT. See THIRD_PARTY_NOTICES.md.
Node streams become synchronous byte streams, called outside the event loop.
"""

from __future__ import annotations

import os
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


def readTranscriptRecords(
    filePath: str,
    provider: str,
    resumeFrom: TranscriptParsePosition | None = None,
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

        def parseLine(
            line: str, state: dict[str, Any], out: list[dict[str, Any]], provisional: bool = False
        ) -> None:
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
