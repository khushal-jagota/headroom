"""Claude assistant-message counters with global message/request identity."""

from __future__ import annotations

import json
from collections.abc import Iterable

from ..contracts import ParsedUsage, UsageRecord, UsageTokens
from .shared import (
    Records,
    issue,
    object_dict,
    projected_records,
    text,
    timestamp,
    token_count,
    usd,
)


def parse_claude(lines: Iterable[str]) -> ParsedUsage:
    records = Records()
    issues: list[str] = []
    for envelope in projected_records("claude", lines):
        if envelope.get("type") == "usage_parse_error":
            issue(issues, "A native Claude usage line is malformed.")
            continue
        message = object_dict(envelope.get("message"))
        message_id = text(message.get("id"))
        request_id = text(envelope.get("requestId"))
        occurred_at = timestamp(envelope.get("timestamp"))
        if (message_id is None and request_id is None) or occurred_at is None:
            issue(issues, "Claude usage lacks valid identity or timestamp.")
            continue
        usage = object_dict(message.get("usage"))
        uncached = token_count(usage.get("input_tokens"))
        cached = token_count(usage.get("cache_read_input_tokens"))
        creation = token_count(usage.get("cache_creation_input_tokens"))
        inclusive = (
            uncached + cached + creation
            if uncached is not None and cached is not None and creation is not None
            else None
        )
        output = token_count(usage.get("output_tokens"))
        details = object_dict(usage.get("output_tokens_details"))
        reasoning = token_count(details.get("thinking_tokens"))
        if reasoning is not None and output is not None and reasoning > output:
            issue(issues, "Claude reasoning exceeds output.")
            reasoning = None
        key = json.dumps([message_id, request_id], separators=(",", ":"))
        records.add(
            UsageRecord(
                provider="claude",
                record_key="claude:message:" + key,
                format="claude_message",
                occurred_at=occurred_at,
                model=text(message.get("model")),
                thread_id=None,
                session_id=text(envelope.get("sessionId")),
                tokens=UsageTokens(inclusive, uncached, output, cached, creation, reasoning),
                reported_cost_usd=usd(envelope.get("costUSD")),
                raw_usage={
                    "message_id": message_id,
                    "request_id": request_id,
                    "usage": usage,
                    "costUSD": usd(envelope.get("costUSD")),
                },
            )
        )
    for diagnostic in records.issues:
        issue(issues, diagnostic)
    return ParsedUsage(
        records=tuple(records.values.values()),
        issues=tuple(issues),
        duplicates=records.duplicates,
        conflicted_record_keys=tuple(sorted(records.conflicts)),
    )
