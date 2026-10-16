"""The contract for native usage ingestion and recorded summaries.

Native transcript records are the only chart source. Conversation token_usage events
and subscription allowance snapshots never become additional consumption records.
Source files are read-only. Only usage fields and provenance enter Panels storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TypedDict

UsageProvider = Literal["codex", "claude"]
UsageFormat = Literal["codex_response", "codex_legacy", "claude_message"]
UsageSourceState = Literal["ready", "partial", "unavailable", "failed"]


@dataclass(frozen=True, slots=True)
class UsageTokens:
    """Input includes cache; output includes reasoning. None means unknown.

    Claude's separate input/cache categories must be normalized into inclusive input.
    Codex's input already includes cache. Never add a subset a second time.
    """

    input_tokens: int | None
    uncached_input_tokens: int | None
    output_tokens: int | None
    cached_input_tokens: int | None
    cache_creation_tokens: int | None
    reasoning_tokens: int | None


@dataclass(frozen=True, slots=True)
class UsageRecord:
    """One identified native response/message, with only usage provenance.

    Modern Codex identity is provider + response_id globally, including copied files.
    Claude identity is provider + message.id/requestId globally. Thread/session IDs
    describe attribution rather than a new copy of the same response. Conflicting
    identities are a coverage problem, never a second consumption record.
    """

    provider: UsageProvider
    record_key: str
    format: UsageFormat
    occurred_at: datetime
    model: str | None
    thread_id: str | None
    session_id: str | None
    tokens: UsageTokens
    reported_cost_usd: float | None
    raw_usage: dict[str, object]


@dataclass(frozen=True, slots=True)
class ParsedUsage:
    """A parsed file/span; modern thread precedence applies across all stored files.

    For a thread with modern response records, exclude its legacy token_count records.
    Legacy-only threads need verified last/cumulative/epoch checks. Ambiguous inherited
    or mixed-format prefixes remain excluded with partial coverage.
    """

    records: tuple[UsageRecord, ...] = ()
    modern_threads: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    duplicates: int = 0
    conflicted_record_keys: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScanLimits:
    """Finite source work for one explicit refresh; limits yield partial coverage."""

    days: int = 90
    max_files: int = 2000
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_line_bytes: int = 2 * 1024 * 1024
    max_seconds: float = 15.0


@dataclass(frozen=True, slots=True)
class UsageSource:
    """A trusted backend home resolved by the server, never a browser-supplied path."""

    provider: UsageProvider
    root: Path


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    provider: UsageProvider
    source_key: str
    state: UsageSourceState
    observed_at: datetime
    records: tuple[UsageRecord, ...] = ()
    modern_threads: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()
    files_scanned: int = 0
    duplicates: int = 0
    conflicted_record_keys: tuple[str, ...] = ()


class UsageMetrics(TypedDict):
    input_tokens: int | None
    uncached_input_tokens: int | None
    cached_input_tokens: int | None
    cache_creation_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    reported_cost_usd: float | None
    record_count: int
    session_count: int | None
    unknown_records: int
    unknown_cost_records: int


class ProviderUsageMetrics(UsageMetrics):
    provider: UsageProvider


class ModelUsageMetrics(ProviderUsageMetrics):
    model: str | None


class PeriodUsageMetrics(UsageMetrics):
    start: str
    end: str
    label: str
    providers: list[ProviderUsageMetrics]


class UsageSourceSummary(TypedDict):
    provider: UsageProvider
    state: UsageSourceState
    observed_at: str | None
    files_scanned: int
    record_count: int
    issues: list[str]
    duplicates: int


class UsageSummary(TypedDict):
    scope: Literal["local_cli"]
    days: int
    timezone: str
    since: str
    until: str
    observed_at: str | None
    sources: list[UsageSourceSummary]
    totals: UsageMetrics
    providers: list[ProviderUsageMetrics]
    models: list[ModelUsageMetrics]
    periods: list[PeriodUsageMetrics]


# Wire names are fixed here before the server and browser implementations. Request:
# GET /api/conversation/usage/summary?days=1|7|30|90&timezone=<IANA name>
# POST /api/conversation/usage/refresh  (no source paths or credentials in the request)
#
# Summary: scope="local_cli", days, timezone, since, until, observed_at,
# sources[{provider,state,observed_at,files_scanned,record_count,issues,duplicates}],
# totals: Metrics, providers[{provider,...Metrics}], models[{provider,model,...Metrics}],
# periods[{start,end,label,providers[{provider,...Metrics}],...Metrics}].
#
# Metrics: input_tokens (inclusive), uncached_input_tokens, cached_input_tokens,
# cache_creation_tokens, output_tokens, reasoning_tokens, total_tokens,
# reported_cost_usd (all nullable), record_count, session_count,
# unknown_records, unknown_cost_records.
#
# A sum with unknown contributing values is null, not zero. Keep independently known
# categories: Claude's uncached input can be known while inclusive input is unknown.
# Empty record sets have null token/cost metrics, never fabricated zero consumption.
# Periods contain observed buckets only. Omitted dates/hours are unknown gaps, not zero
# points; charts must not interpolate across an uncovered interval. Source readiness
# describes a bounded local read, not a complete account ledger. Provider/source coverage
# remains separate from record-level unknowns. No price estimator, quota history, or
# Hermes time series.
