"""Usage-only snapshots. Native content, credentials, and source paths stay outside."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import cast

from planner.conversation.usage.contracts import (
    ParsedUsage,
    SourceSnapshot,
    UsageFormat,
    UsageProvider,
    UsageRecord,
    UsageSourceState,
    UsageTokens,
)

# Raw usage is retained only through this field whitelist. Provider payloads can contain
# conversation text beside these fields, so serializing a generic payload is forbidden.
_USAGE_FIELDS = frozenset({
    "input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
    "cache_creation_input_tokens", "cache_read_input_tokens", "cache_creation_tokens",
    "cache_write_input_tokens", "reasoning_tokens", "reasoning_output_tokens",
    "inputTokens", "outputTokens", "totalTokens", "cachedInputTokens",
    "cacheWriteInputTokens", "reasoningOutputTokens", "cacheReadInputTokens",
    "cacheCreationInputTokens", "input_tokens_details", "output_tokens_details",
    "cached_tokens", "cache_creation", "ephemeral_5m_input_tokens",
    "ephemeral_1h_input_tokens", "cost_usd", "total_cost_usd", "costUSD",
    "usage", "last_token_usage", "total_token_usage", "turn_token_usage",
    "thread_token_usage", "thinking_tokens",
})
_PROVENANCE_FIELDS = frozenset({
    "thread_id", "turn_id", "session_id", "root_turn_id", "response_id",
    "message_id", "request_id",
})


def _usage_fields(raw: dict[str, object]) -> dict[str, object]:
    kept: dict[str, object] = {}
    for key, value in raw.items():
        if key in _PROVENANCE_FIELDS and (value is None or isinstance(value, str)):
            kept[key] = value
            continue
        if key not in _USAGE_FIELDS:
            continue
        if isinstance(value, dict):
            kept[key] = _usage_fields(cast(dict[str, object], value))
        elif value is None or isinstance(value, (int, float, bool)):
            kept[key] = value
    return kept


def record_json(record: UsageRecord) -> dict[str, object]:
    return {
        "provider": record.provider, "record_key": record.record_key, "format": record.format,
        "occurred_at": record.occurred_at.isoformat(), "model": record.model,
        "thread_id": record.thread_id, "session_id": record.session_id,
        "tokens": asdict(record.tokens), "reported_cost_usd": record.reported_cost_usd,
        "raw_usage": _usage_fields(record.raw_usage),
    }


def _record(raw: dict[str, object]) -> UsageRecord:
    tokens = cast(dict[str, int | None], raw["tokens"])
    return UsageRecord(
        provider=cast(UsageProvider, raw["provider"]), record_key=str(raw["record_key"]),
        format=cast(UsageFormat, raw["format"]),
        occurred_at=datetime.fromisoformat(str(raw["occurred_at"])),
        model=cast(str | None, raw["model"]),
        thread_id=cast(str | None, raw["thread_id"]),
        session_id=cast(str | None, raw["session_id"]),
        tokens=UsageTokens(**tokens),
        reported_cost_usd=cast(float | None, raw["reported_cost_usd"]),
        raw_usage=cast(dict[str, object], raw["raw_usage"]),
    )


def _parsed_json(parsed: ParsedUsage) -> dict[str, object]:
    return {
        "records": [record_json(record) for record in parsed.records],
        "modern_threads": parsed.modern_threads, "issues": parsed.issues,
        "duplicates": parsed.duplicates,
        "conflicted_record_keys": parsed.conflicted_record_keys,
    }


def _parsed(raw: dict[str, object]) -> ParsedUsage:
    return ParsedUsage(
        records=tuple(_record(record) for record in cast(list[dict[str, object]], raw["records"])),
        modern_threads=tuple(cast(list[str], raw["modern_threads"])),
        issues=tuple(cast(list[str], raw["issues"])), duplicates=int(str(raw["duplicates"])),
        conflicted_record_keys=tuple(cast(list[str], raw.get("conflicted_record_keys", []))),
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


def read_files(conn: sqlite3.Connection) -> tuple[FileSnapshot, ...]:
    snapshots: list[FileSnapshot] = []
    for row in conn.execute("SELECT provider,source_key,file_key,snapshot FROM native_usage_files"):
        raw = json.loads(row["snapshot"])
        snapshots.append(FileSnapshot(
            provider=row["provider"], source_key=row["source_key"], file_key=row["file_key"],
            device=raw["device"], inode=raw["inode"], size=raw["size"],
            mtime_ns=raw["mtime_ns"], ctime_ns=raw["ctime_ns"], offset=raw["offset"],
            digest=raw["digest"], projected_lines=tuple(raw["projected_lines"]),
            parsed=_parsed(raw["parsed"]),
        ))
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
        issues = tuple(dict.fromkeys((
            *raw["issues"], *(issue for parsed in matching for issue in parsed.issues)
        )))
        state = cast(UsageSourceState, raw["state"])
        if issues and state == "ready":
            state = "partial"
        sources.append(SourceSnapshot(
            provider=row["provider"], source_key=row["source_key"],
            state=state,
            observed_at=datetime.fromisoformat(raw["observed_at"]),
            records=tuple(record for parsed in matching for record in parsed.records),
            modern_threads=tuple(
                thread for parsed in matching for thread in parsed.modern_threads
            ),
            conflicted_record_keys=tuple(
                key for parsed in matching for key in parsed.conflicted_record_keys
            ),
            issues=issues, files_scanned=raw["files_scanned"],
            duplicates=sum(parsed.duplicates for parsed in matching),
        ))
    return tuple(sources)


def save_refresh(
    conn: sqlite3.Connection, sources: tuple[SourceSnapshot, ...], files: tuple[FileSnapshot, ...]
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        for file in files:
            raw = {
                "device": file.device, "inode": file.inode, "size": file.size,
                "mtime_ns": file.mtime_ns, "ctime_ns": file.ctime_ns,
                "offset": file.offset, "digest": file.digest,
                "projected_lines": file.projected_lines, "parsed": _parsed_json(file.parsed),
            }
            conn.execute(
                "INSERT INTO native_usage_files(provider,source_key,file_key,snapshot) "
                "VALUES (?,?,?,?) ON CONFLICT(provider,source_key,file_key) "
                "DO UPDATE SET snapshot=excluded.snapshot",
                (file.provider, file.source_key, file.file_key, json.dumps(raw, allow_nan=False)),
            )
        for source in sources:
            raw = {
                "state": source.state, "observed_at": source.observed_at.isoformat(),
                "issues": source.issues, "files_scanned": source.files_scanned,
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
