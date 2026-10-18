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
UsageSpeed = Literal["standard", "fast", "ultrafast"]
UsageCostSource = Literal["unpriced", "model_priced", "provider_reported"]


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
    speed: UsageSpeed = "standard"


class UsageCategoryCost(TypedDict):
    input: float
    cache_read: float
    cache_write: float
    output: float


class UsageSpeedCost(TypedDict):
    standard: float
    fast: float
    ultrafast: float


@dataclass(frozen=True, slots=True)
class UsagePriceSnapshot:
    """The full external price document that entered Panels-owned state."""

    source: str
    fetched_at: datetime
    document: dict[str, object]


class UsagePricingSummary(TypedDict):
    status: Literal["fresh", "cached", "unavailable"]
    source: str
    fetched_at: str | None
    known_models: int


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
    max_total_bytes: int = 256 * 1024 * 1024
    max_seconds: float = 15.0
    chunk_bytes: int = 256 * 1024
    file_slice_bytes: int = 8 * 1024 * 1024
    max_discovery_entries: int = 100000


@dataclass(frozen=True, slots=True)
class UsageSource:
    """A trusted backend home resolved by the server, never a browser-supplied path."""

    provider: UsageProvider
    root: Path


class UsageReadProgress(TypedDict):
    files_total: int
    files_completed: int
    bytes_total: int
    bytes_read: int
    more_work: bool


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
    progress: UsageReadProgress | None = None


class UsageMetrics(TypedDict):
    input_tokens: int | None
    uncached_input_tokens: int | None
    cached_input_tokens: int | None
    cache_creation_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    total_tokens: int | None
    reported_cost_usd: float | None
    cost_usd: float | None
    estimated_cost_usd: float | None
    provider_reported_cost_usd: float | None
    category_cost_usd: UsageCategoryCost | None
    speed_cost_usd: UsageSpeedCost | None
    cache_savings_usd: float | None
    speed_premium_usd: float | None
    unpriced_records: int
    model_priced_records: int
    provider_reported_records: int
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
    progress: UsageReadProgress | None


class UsageSummary(TypedDict):
    scope: Literal["local_cli"]
    days: int
    timezone: str
    since: str
    until: str
    observed_at: str | None
    pricing: UsagePricingSummary
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
# remains separate from record-level unknowns. Cost uses reported USD when present,
# otherwise the persisted T3/LiteLLM model-price table. Known cost subtotals stay visible
# with unpriced record counts. Estimated and reported subtotals remain separate.
# No account billing claim, quota history, or Hermes time series.
