"""Usage-only snapshots. Native content, credentials, and source paths stay outside."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from planner.conversation.usage.codec import _parsed, _parsed_json
from planner.conversation.usage.codec import record_json as record_json
from planner.conversation.usage.contracts import (
    ParsedUsage,
    SourceSnapshot,
    UsageProvider,
    UsageSourceState,
)


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """An internal cursor over complete lines, plus projections that contain no content."""

    provider: UsageProvider
    source_key: str
    file_key: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    offset: int
    digest: str
    projected_lines: tuple[str, ...]
    parsed: ParsedUsage
    pending: dict[str, object] | None = None
    blocks: tuple[tuple[int, str], ...] = ()
    stream_state: dict[str, object] | None = None


def read_files(conn: sqlite3.Connection) -> tuple[FileSnapshot, ...]:
    snapshots: list[FileSnapshot] = []
    for row in conn.execute("SELECT provider,source_key,file_key,snapshot FROM native_usage_files"):
        raw = json.loads(row["snapshot"])
        snapshots.append(
            FileSnapshot(
                provider=row["provider"],
                source_key=row["source_key"],
                file_key=row["file_key"],
                device=raw["device"],
                inode=raw["inode"],
                size=raw["size"],
                mtime_ns=raw["mtime_ns"],
                ctime_ns=raw["ctime_ns"],
                offset=raw["offset"],
                digest=raw["digest"],
                projected_lines=tuple(raw["projected_lines"]),
                parsed=_parsed(raw["parsed"]),
                pending=raw.get("pending"),
                blocks=tuple(tuple(block) for block in raw.get("blocks", [])),
                stream_state=raw.get("stream_state"),
            )
        )
    return tuple(snapshots)


def read_sources(conn: sqlite3.Connection) -> tuple[SourceSnapshot, ...]:
    # A screen needs records and coverage, not the potentially much larger projection
    # cache that the next explicit append read uses.
    files = tuple(
        (row["provider"], _parsed(json.loads(row["parsed"])))
        for row in conn.execute(
            "SELECT provider,json_extract(snapshot,'$.parsed') AS parsed FROM native_usage_files"
        )
    )
    sources: list[SourceSnapshot] = []
    for row in conn.execute("SELECT provider,source_key,snapshot FROM native_usage_sources"):
        raw = json.loads(row["snapshot"])
        # Previously verified data survives source errors, missing files, and home changes.
        matching = [parsed for provider, parsed in files if provider == row["provider"]]
        issues = tuple(
            dict.fromkeys(
                (*raw["issues"], *(issue for parsed in matching for issue in parsed.issues))
            )
        )
        state = cast(UsageSourceState, raw["state"])
        if issues and state == "ready":
            state = "partial"
        sources.append(
            SourceSnapshot(
                provider=row["provider"],
                source_key=row["source_key"],
                state=state,
                observed_at=datetime.fromisoformat(raw["observed_at"]),
                records=tuple(record for parsed in matching for record in parsed.records),
                modern_threads=tuple(
                    thread for parsed in matching for thread in parsed.modern_threads
                ),
                conflicted_record_keys=tuple(
                    key for parsed in matching for key in parsed.conflicted_record_keys
                ),
                issues=issues,
                files_scanned=raw["files_scanned"],
                duplicates=sum(parsed.duplicates for parsed in matching),
                progress=raw.get("progress"),
            )
        )
    return tuple(sources)


def read_scan_states(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    """Private progress contains hashed file identities, never native source paths."""
    return {
        row["provider"]: raw["scan"]
        for row in conn.execute("SELECT provider,snapshot FROM native_usage_sources")
        for raw in (json.loads(row["snapshot"]),)
        if isinstance(raw.get("scan"), dict)
    }


def save_refresh(
    conn: sqlite3.Connection,
    sources: tuple[SourceSnapshot, ...],
    files: tuple[FileSnapshot, ...],
    scan_states: dict[str, dict[str, object]] | None = None,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        for file in files:
            raw = {
                "device": file.device,
                "inode": file.inode,
                "size": file.size,
                "mtime_ns": file.mtime_ns,
                "ctime_ns": file.ctime_ns,
                "offset": file.offset,
                "digest": file.digest,
                "projected_lines": file.projected_lines,
                "parsed": _parsed_json(file.parsed),
                "pending": file.pending,
                "blocks": file.blocks,
                "stream_state": file.stream_state,
            }
            conn.execute(
                "INSERT INTO native_usage_files(provider,source_key,file_key,snapshot) "
                "VALUES (?,?,?,?) ON CONFLICT(provider,source_key,file_key) "
                "DO UPDATE SET snapshot=excluded.snapshot",
                (file.provider, file.source_key, file.file_key, json.dumps(raw, allow_nan=False)),
            )
        for source in sources:
            raw = {
                "state": source.state,
                "observed_at": source.observed_at.isoformat(),
                "issues": source.issues,
                "files_scanned": source.files_scanned,
                "progress": source.progress,
                "scan": (scan_states or {}).get(source.provider),
            }
            conn.execute(
                "INSERT INTO native_usage_sources(provider,source_key,snapshot) VALUES (?,?,?) "
                "ON CONFLICT(provider) DO UPDATE SET source_key=excluded.source_key, "
                "snapshot=excluded.snapshot",
                (source.provider, source.source_key, json.dumps(raw, allow_nan=False)),
            )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
