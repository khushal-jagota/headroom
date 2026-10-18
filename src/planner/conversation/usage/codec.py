"""Content-free usage records shared by storage and incremental parser checkpoints."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import cast

from planner.conversation.usage.contracts import (
    ParsedUsage,
    UsageFormat,
    UsageProvider,
    UsageRecord,
    UsageSpeed,
    UsageTokens,
)

# Raw usage is retained only through this field whitelist. Provider payloads can contain
# conversation text beside these fields, so serializing a generic payload is forbidden.
_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "cache_creation_tokens",
        "cache_write_input_tokens",
        "reasoning_tokens",
        "reasoning_output_tokens",
        "inputTokens",
        "outputTokens",
        "totalTokens",
        "cachedInputTokens",
        "cacheWriteInputTokens",
        "reasoningOutputTokens",
        "cacheReadInputTokens",
        "cacheCreationInputTokens",
        "input_tokens_details",
        "output_tokens_details",
        "cached_tokens",
        "cache_creation",
        "ephemeral_5m_input_tokens",
        "ephemeral_1h_input_tokens",
        "cost_usd",
        "total_cost_usd",
        "costUSD",
        "usage",
        "last_token_usage",
        "total_token_usage",
        "turn_token_usage",
        "thread_token_usage",
        "thinking_tokens",
    }
)
_PROVENANCE_FIELDS = frozenset(
    {
        "thread_id",
        "turn_id",
        "session_id",
        "root_turn_id",
        "response_id",
        "message_id",
        "request_id",
    }
)


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
        "provider": record.provider,
        "record_key": record.record_key,
        "format": record.format,
        "occurred_at": record.occurred_at.isoformat(),
        "model": record.model,
        "thread_id": record.thread_id,
        "session_id": record.session_id,
        "tokens": asdict(record.tokens),
        "reported_cost_usd": record.reported_cost_usd,
        "raw_usage": _usage_fields(record.raw_usage),
        "speed": record.speed,
    }


def _record(raw: dict[str, object]) -> UsageRecord:
    tokens = cast(dict[str, int | None], raw["tokens"])
    return UsageRecord(
        provider=cast(UsageProvider, raw["provider"]),
        record_key=str(raw["record_key"]),
        format=cast(UsageFormat, raw["format"]),
        occurred_at=datetime.fromisoformat(str(raw["occurred_at"])),
        model=cast(str | None, raw["model"]),
        thread_id=cast(str | None, raw["thread_id"]),
        session_id=cast(str | None, raw["session_id"]),
        tokens=UsageTokens(**tokens),
        reported_cost_usd=cast(float | None, raw["reported_cost_usd"]),
        raw_usage=cast(dict[str, object], raw["raw_usage"]),
        speed=cast(UsageSpeed, raw.get("speed", "standard")),
    )


def _parsed_json(parsed: ParsedUsage) -> dict[str, object]:
    return {
        "records": [record_json(record) for record in parsed.records],
        "modern_threads": parsed.modern_threads,
        "issues": parsed.issues,
        "duplicates": parsed.duplicates,
        "conflicted_record_keys": parsed.conflicted_record_keys,
    }


def _parsed(raw: dict[str, object]) -> ParsedUsage:
    return ParsedUsage(
        records=tuple(_record(record) for record in cast(list[dict[str, object]], raw["records"])),
        modern_threads=tuple(cast(list[str], raw["modern_threads"])),
        issues=tuple(cast(list[str], raw["issues"])),
        duplicates=int(str(raw["duplicates"])),
        conflicted_record_keys=tuple(cast(list[str], raw.get("conflicted_record_keys", []))),
    )
