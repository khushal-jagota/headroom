"""Codex's identified response ledger, with a conservative legacy-only path."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..contracts import ParsedUsage, UsageRecord, UsageTokens
from .shared import Records, issue, object_dict, projected_records, text, timestamp, token_count


def _tokens(usage: dict[str, object], issues: list[str]) -> UsageTokens:
    inclusive = token_count(usage.get("input_tokens"))
    cached = token_count(usage.get("cached_input_tokens"))
    creation = token_count(usage.get("cache_write_input_tokens"))
    output = token_count(usage.get("output_tokens"))
    reasoning = token_count(usage.get("reasoning_output_tokens"))
    uncached = None
    if inclusive is not None and cached is not None and creation is not None:
        if cached + creation <= inclusive:
            uncached = inclusive - cached - creation
        else:
            issue(issues, "Codex cache categories exceed inclusive input.")
            inclusive = None
    if reasoning is not None and output is not None and reasoning > output:
        issue(issues, "Codex reasoning exceeds output.")
        reasoning = None
    total = token_count(usage.get("total_tokens"))
    if inclusive is not None and output is not None and total is not None:
        if total != inclusive + output:
            issue(issues, "Codex total disagrees with input and output.")
    return UsageTokens(inclusive, uncached, output, cached, creation, reasoning)


@dataclass(slots=True)
class _LegacyState:
    thread_id: str | None = None
    session_id: str | None = None
    saw_meta: bool = False
    forked: bool = False
    epoch: int = 0
    previous: dict[str, object] | None = None


def _forked(payload: dict[str, object]) -> bool:
    source = object_dict(payload.get("source"))
    spawn = object_dict(object_dict(source.get("subagent")).get("thread_spawn"))
    return any(
        text(value) is not None
        for value in (
            payload.get("forked_from_id"),
            payload.get("parent_thread_id"),
            spawn.get("parent_thread_id"),
        )
    )


def _required(usage: dict[str, object]) -> tuple[int, int] | None:
    inclusive = token_count(usage.get("input_tokens"))
    output = token_count(usage.get("output_tokens"))
    total = token_count(usage.get("total_tokens"))
    if inclusive is None or output is None:
        return None
    if "total_tokens" in usage and (total is None or total != inclusive + output):
        return None
    return inclusive, output


def _matches_delta(
    last: dict[str, object],
    cumulative: dict[str, object],
    previous: dict[str, object] | None,
) -> bool:
    for key in (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ):
        delta = token_count(last.get(key))
        current = token_count(cumulative.get(key))
        baseline = 0 if previous is None else token_count(previous.get(key))
        if delta is not None and current is not None and baseline is not None:
            if current - baseline != delta:
                return False
    return True


def _legacy_record(
    payload: dict[str, object],
    occurred_at: datetime | None,
    model: str | None,
    state: _LegacyState,
    issues: list[str],
) -> tuple[UsageRecord | None, bool]:
    info = object_dict(payload.get("info"))
    last = object_dict(info.get("last_token_usage"))
    cumulative = object_dict(info.get("total_token_usage"))
    if not last and not cumulative:
        return None, False  # Rate-limit-only notifications are not responses.
    current = _required(cumulative)
    previous = _required(state.previous) if state.previous is not None else None
    if current is None:
        issue(issues, "Legacy Codex cumulative counters are unknown or inconsistent.")
        state.previous = None
        return None, False
    if previous == current:
        # A rate-limit update can repeat the last actual response. A context
        # estimate can also change last_token_usage without new consumption.
        return None, True
    if previous is not None and any(a < b for a, b in zip(current, previous, strict=True)):
        state.epoch += 1
        state.previous = cumulative
        issue(issues, "Legacy Codex counter reset has an uncertain baseline.")
        return None, False
    baseline = state.previous
    state.previous = cumulative
    if state.forked:
        issue(issues, "Legacy Codex fork history cannot be verified.")
        return None, False
    if state.thread_id is None:
        issue(issues, "Legacy Codex usage has no native thread identity.")
        return None, False
    if _required(last) is None or not _matches_delta(last, cumulative, baseline):
        issue(issues, "Legacy Codex usage lacks a verified request delta or baseline.")
        return None, False
    if occurred_at is None:
        issue(issues, "Legacy Codex usage has no valid timestamp.")
        return None, False
    if model is None:
        issue(issues, "Legacy Codex usage has no model attribution.")
        return None, False
    if current == (0, 0):
        return None, False
    tokens = _tokens(last, issues)
    key = json.dumps([state.thread_id, state.epoch, *current], separators=(",", ":"))
    return UsageRecord(
        provider="codex",
        record_key="codex:legacy:" + key,
        format="codex_legacy",
        occurred_at=occurred_at,
        model=model,
        thread_id=state.thread_id,
        session_id=state.session_id,
        tokens=tokens,
        reported_cost_usd=None,
        raw_usage={"last_token_usage": last, "total_token_usage": cumulative},
    ), False


def parse_codex(lines: Iterable[str]) -> ParsedUsage:
    """Count each native response once; never add its legacy representation."""
    modern = Records()
    legacy = Records()
    state = _LegacyState()
    issues: list[str] = []
    legacy_issues: list[str] = []
    legacy_signatures: set[str] = set()
    model: str | None = None
    turn_models: dict[str, str] = {}
    modern_threads: set[str] = set()
    saw_modern = False
    saw_legacy_usage = False
    for record in projected_records("codex", lines):
        kind = record.get("type")
        payload = object_dict(record.get("payload"))
        if kind == "usage_parse_error":
            issue(issues, "A native Codex usage line is malformed.")
        elif kind == "session_meta" and not state.saw_meta:
            state.saw_meta = True
            state.thread_id = text(payload.get("id"))
            state.session_id = text(payload.get("session_id"))
            state.forked = _forked(payload)
        elif kind == "turn_context":
            model = text(payload.get("model"))
            turn_id = text(payload.get("turn_id"))
            if turn_id is not None and model is not None:
                turn_models[turn_id] = model
        elif kind == "token_usage_record":
            saw_modern = True
            thread_id = text(payload.get("thread_id"))
            if thread_id is not None:
                modern_threads.add(thread_id)
            else:
                issue(issues, "A Codex response has no original thread identity.")
                if state.thread_id is not None:
                    modern_threads.add(state.thread_id)
                # A response without its original thread cannot establish
                # cross-file precedence over legacy consumption. Exclude this
                # ambiguous record, and all legacy candidates in this span.
                continue
            response_id = text(payload.get("response_id"))
            occurred_at = timestamp(record.get("timestamp"))
            if response_id is None or occurred_at is None:
                issue(issues, "A Codex response lacks valid identity or timestamp.")
                continue
            usage = object_dict(payload.get("usage"))
            turn_id = text(payload.get("turn_id"))
            response_model = turn_models.get(turn_id) if turn_id is not None else None
            if response_model is None:
                issue(issues, "A Codex response has no verified model for its native turn.")
            modern.add(
                UsageRecord(
                    provider="codex",
                    record_key="codex:response:" + response_id,
                    format="codex_response",
                    occurred_at=occurred_at,
                    model=response_model,
                    thread_id=thread_id,
                    session_id=text(payload.get("session_id")),
                    tokens=_tokens(usage, issues),
                    reported_cost_usd=None,
                    raw_usage=payload,
                )
            )
        elif payload.get("type") == "token_count":
            info = object_dict(payload.get("info"))
            last = object_dict(info.get("last_token_usage"))
            cumulative = object_dict(info.get("total_token_usage"))
            saw_legacy_usage = saw_legacy_usage or bool(last or cumulative)
            if _required(last) not in (None, (0, 0)) and _required(cumulative) is not None:
                legacy_signatures.add(json.dumps([last, cumulative], sort_keys=True))
            candidate, duplicate = _legacy_record(
                payload,
                timestamp(record.get("timestamp")),
                model,
                state,
                legacy_issues,
            )
            legacy.duplicates += int(duplicate)
            if candidate is not None:
                legacy.add(candidate)
    records = list(modern.values.values())
    if saw_modern:
        # Legacy records lack response IDs, so unmatched emissions cannot prove
        # an extra response in a mixed/upgrade span. Storage extends this rule
        # across files using modern_threads.
        matching_responses = {
            json.dumps(
                [
                    item.raw_usage.get("usage"),
                    item.raw_usage.get("thread_token_usage"),
                ],
                sort_keys=True,
            )
            for item in modern.values.values()
        }
        if legacy_signatures - matching_responses:
            issue(issues, "Mixed Codex history has an uncertain legacy-only prefix.")
    else:
        records.extend(legacy.values.values())
        if saw_legacy_usage:
            issue(issues, "Legacy Codex usage has limited producer provenance.")
        for message in (*legacy_issues, *legacy.issues):
            issue(issues, message)
    for message in modern.issues:
        issue(issues, message)
    return ParsedUsage(
        records=tuple(records),
        modern_threads=tuple(sorted(modern_threads)),
        issues=tuple(issues),
        duplicates=modern.duplicates + legacy.duplicates,
        conflicted_record_keys=tuple(
            sorted(modern.conflicts | (legacy.conflicts if not saw_modern else set()))
        ),
    )
