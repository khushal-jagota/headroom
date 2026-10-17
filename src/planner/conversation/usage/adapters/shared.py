"""The narrow, content-free boundary around native transcript records."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Iterator
from dataclasses import replace
from datetime import UTC, datetime

from ..contracts import ParsedUsage, UsageProvider, UsageRecord

COUNTER_NAMES = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_tokens",
)


def object_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {key: item for key, item in value.items() if isinstance(key, str)}


def text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def token_count(value: object) -> int | None:
    # bool is an int in Python. Neither it nor a fractional count is usage.
    if type(value) is int:
        return value if value >= 0 else None
    if type(value) is float and math.isfinite(value) and value >= 0 and value.is_integer():
        return int(value)
    return None


def usd(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except OverflowError:
        return None
    return amount if math.isfinite(amount) and amount >= 0 else None


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(UTC) if result.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def issue(issues: list[str], message: str) -> None:
    if message not in issues:
        issues.append(message)


def _counters(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    source = object_dict(value)
    return {key: token_count(source[key]) for key in COUNTER_NAMES if key in source}


def _selected(source: dict[str, object], names: Iterable[str]) -> dict[str, object]:
    return {key: text(source[key]) for key in names if key in source}


def project_usage_line(provider: UsageProvider, line: str) -> str | None:
    """Discard message/tool bodies before any line enters the scan cache.

    An invalid relevant line becomes a content-free sentinel. Replaying cached
    projections therefore preserves the coverage issue without retaining its text.
    Invalid scalar counters become null, never strings or fabricated zero values.
    """
    markers = (
        (
            '"token_usage_record"',
            '"token_count"',
            '"turn_context"',
            '"session_meta"',
            '"thread_settings_applied"',
        )
        if provider == "codex"
        else ('"usage"',)
    )
    if '"usage_parse_error"' in line:
        markers = (*markers, '"usage_parse_error"')
    if not any(marker in line for marker in markers):
        return None
    try:
        raw: object = json.loads(line)
    except (ValueError, RecursionError):
        return '{"type":"usage_parse_error"}'
    record = object_dict(raw)
    kind = record.get("type")
    if kind == "usage_parse_error":
        return '{"type":"usage_parse_error"}'
    selected: dict[str, object] = {"type": kind}
    if "timestamp" in record:
        selected["timestamp"] = text(record["timestamp"])
    if provider == "claude":
        message = object_dict(record.get("message"))
        if kind != "assistant" or "usage" not in message:
            return None
        selected.update(_selected(record, ("requestId", "sessionId")))
        usage = _counters(message.get("usage"))
        raw_usage = object_dict(message.get("usage"))
        if usage is not None and "speed" in raw_usage:
            usage["speed"] = "fast" if raw_usage["speed"] == "fast" else "standard"
        if usage is not None and "output_tokens_details" in raw_usage:
            details = object_dict(raw_usage["output_tokens_details"])
            usage["output_tokens_details"] = {
                "thinking_tokens": token_count(details.get("thinking_tokens"))
            }
        selected["message"] = {
            **_selected(message, ("id", "model")),
            "usage": usage,
        }
        if "costUSD" in record:
            selected["costUSD"] = usd(record["costUSD"])
    else:
        payload = object_dict(record.get("payload"))
        projected: dict[str, object]
        if kind == "session_meta":
            projected = _selected(
                payload,
                ("id", "session_id", "forked_from_id", "parent_thread_id", "cli_version"),
            )
            source = object_dict(payload.get("source"))
            spawn = object_dict(object_dict(source.get("subagent")).get("thread_spawn"))
            if "parent_thread_id" in spawn:
                projected["source"] = {
                    "subagent": {"thread_spawn": _selected(spawn, ("parent_thread_id",))}
                }
        elif kind == "turn_context":
            projected = _selected(payload, ("model", "turn_id"))
        elif payload.get("type") == "thread_settings_applied":
            tier = object_dict(payload.get("thread_settings")).get("service_tier")
            projected = {
                "type": "thread_settings_applied",
                "thread_settings": {
                    "service_tier": (
                        tier if tier in ("priority", "fast", "ultrafast") else "standard"
                    )
                },
            }
        elif kind == "token_usage_record":
            projected = _selected(
                payload, ("thread_id", "turn_id", "session_id", "root_turn_id", "response_id")
            )
            for key in ("usage", "turn_token_usage", "thread_token_usage"):
                if key in payload:
                    projected[key] = _counters(payload[key])
        elif kind == "event_msg" and payload.get("type") == "token_count":
            info = object_dict(payload.get("info"))
            projected = {
                "type": "token_count",
                "info": {
                    key: _counters(info[key])
                    for key in ("last_token_usage", "total_token_usage")
                    if key in info
                },
            }
        else:
            return None
        selected["payload"] = projected
    return json.dumps(selected, separators=(",", ":"), allow_nan=False)


def projected_records(provider: UsageProvider, lines: Iterable[str]) -> Iterator[dict[str, object]]:
    for line in lines:
        projected = project_usage_line(provider, line)
        if projected is not None:
            yield object_dict(json.loads(projected))


class Records:
    """Reject conflicting identity data instead of selecting an arbitrary amount."""

    def __init__(self, conflicted_record_keys: Iterable[str] = ()) -> None:
        self.values: dict[str, UsageRecord] = {}
        self.conflicts = set(conflicted_record_keys)
        self.duplicates = 0
        self.issues: list[str] = []
        self._claude_aliases: dict[tuple[str, str], set[str]] = {}
        self._aliases_by_key: dict[str, set[tuple[str, str]]] = {}
        for key in self.conflicts:
            prefix = "claude:message:"
            if not key.startswith(prefix):
                continue
            try:
                identity: object = json.loads(key[len(prefix) :])
            except (ValueError, RecursionError):
                continue
            if isinstance(identity, list) and len(identity) == 2:
                self._register_aliases(key, text(identity[0]), text(identity[1]))
        if self.conflicts:
            issue(self.issues, "Previously conflicting usage identities remain excluded.")

    def checkpoint(self) -> dict[str, object]:
        from ..codec import record_json

        return {
            "records": [record_json(record) for record in self.values.values()],
            "conflicts": sorted(self.conflicts),
            "duplicates": self.duplicates,
            "issues": self.issues,
        }

    @classmethod
    def restore(cls, saved: dict[str, object]) -> Records:
        from typing import cast

        from ..codec import _record

        records = cls(cast(list[str], saved.get("conflicts", [])))
        for value in cast(list[dict[str, object]], saved.get("records", [])):
            records.add(_record(value))
        records.duplicates = int(str(saved.get("duplicates", 0)))
        records.issues = list(cast(list[str], saved.get("issues", [])))
        return records

    def _register_aliases(
        self,
        key: str,
        message_id: str | None,
        request_id: str | None,
    ) -> None:
        aliases = self._aliases_by_key.setdefault(key, set())
        for kind, value in (("message", message_id), ("request", request_id)):
            if value is not None:
                alias = kind, value
                aliases.add(alias)
                self._claude_aliases.setdefault(alias, set()).add(key)

    def _claude_overlap(self, record: UsageRecord) -> bool:
        key = record.record_key
        self._register_aliases(
            key,
            text(record.raw_usage.get("message_id")),
            text(record.raw_usage.get("request_id")),
        )
        pending = [key]
        connected: set[str] = set()
        while pending:
            candidate = pending.pop()
            if candidate in connected:
                continue
            connected.add(candidate)
            for alias in self._aliases_by_key.get(candidate, ()):
                pending.extend(self._claude_aliases[alias] - connected)
        if len(connected) <= 1:
            return key in self.conflicts
        # A half identity, a changed request ID, or reused request alias does
        # not establish a second message. Exclude every connected key rather
        # than select an arbitrary complete/partial copy. Retain aliases after
        # exclusion so later files cannot resurrect another overlapping key.
        self.conflicts.update(connected)
        for candidate in connected:
            self.values.pop(candidate, None)
        issue(self.issues, "Overlapping Claude usage identities were excluded.")
        return True

    def add(self, record: UsageRecord) -> None:
        key = record.record_key
        if record.provider == "claude" and self._claude_overlap(record):
            return
        if key in self.conflicts:
            return
        previous = self.values.get(key)
        if previous is None:
            self.values[key] = record
            return
        same = (
            previous.tokens == record.tokens
            and previous.reported_cost_usd == record.reported_cost_usd
            and previous.model == record.model
            and previous.speed == record.speed
        )
        if record.provider == "codex":
            same = (
                same
                and previous.thread_id == record.thread_id
                and previous.session_id == record.session_id
                and previous.raw_usage.get("turn_id") == record.raw_usage.get("turn_id")
                and previous.raw_usage.get("root_turn_id") == record.raw_usage.get("root_turn_id")
            )
        if not same:
            del self.values[key]
            self.conflicts.add(key)
            issue(self.issues, f"Conflicting {record.provider} usage identity was excluded.")
            return
        self.duplicates += 1
        self.values[key] = replace(
            previous, occurred_at=min(previous.occurred_at, record.occurred_at)
        )
        if record.provider == "claude" and previous.session_id != record.session_id:
            self.values[key] = replace(self.values[key], session_id=None)
            issue(self.issues, "Copied Claude usage has uncertain session attribution.")


def deduplicate_usage_records(
    records: Iterable[UsageRecord],
    conflicted_record_keys: Iterable[str] = (),
) -> ParsedUsage:
    """Use the parser identity policy across stored files, including old conflicts.

    Modern-thread precedence remains the caller's separate decision. Persisted
    Claude keys encode both aliases, so a conflict still suppresses a newly seen
    partial/full copy after the original conflicting records leave the scan.
    """
    result = Records(conflicted_record_keys)
    for record in records:
        result.add(record)
    return ParsedUsage(
        records=tuple(result.values.values()),
        issues=tuple(result.issues),
        duplicates=result.duplicates,
        conflicted_record_keys=tuple(sorted(result.conflicts)),
    )
