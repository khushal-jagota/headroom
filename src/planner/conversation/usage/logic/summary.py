"""Summaries of identified native records, with unknown values left unknown."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, time, timedelta
from math import isfinite
from typing import cast
from zoneinfo import ZoneInfo

from planner.conversation.usage.adapters import deduplicate_usage_records
from planner.conversation.usage.contracts import (
    ModelUsageMetrics,
    PeriodUsageMetrics,
    ProviderUsageMetrics,
    SourceSnapshot,
    UsageCategoryCost,
    UsageMetrics,
    UsagePriceSnapshot,
    UsageProvider,
    UsageRecord,
    UsageSourceSummary,
    UsageSummary,
)
from planner.conversation.usage.logic.pricing import RateTable, parse_rate_table, price_usage
from planner.conversation.usage.pricing import pricing_summary

PROVIDERS: tuple[UsageProvider, ...] = ("codex", "claude")


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def verified_records(
    records: Iterable[UsageRecord], modern_threads: Iterable[str],
    conflicted_keys: Iterable[tuple[UsageProvider, str]] = (),
) -> tuple[tuple[UsageRecord, ...], dict[UsageProvider, int], set[UsageProvider]]:
    """Deduplicate globally, exclude conflicts, then apply global modern precedence."""
    modern = set(modern_threads)
    candidates = tuple(records)
    conflicts = tuple(conflicted_keys)
    kept: list[UsageRecord] = []
    uncertain: set[UsageProvider] = set()
    duplicates: dict[UsageProvider, int] = {provider: 0 for provider in PROVIDERS}
    for provider in PROVIDERS:
        parsed = deduplicate_usage_records(
            (record for record in candidates if record.provider == provider),
            (key for owner, key in conflicts if owner == provider),
        )
        duplicates[provider] = parsed.duplicates
        if parsed.issues:
            uncertain.add(provider)
        kept.extend(
            record for record in parsed.records
            if not (record.format == "codex_legacy" and record.thread_id in modern)
        )
    return tuple(kept), duplicates, uncertain


def metrics(records: Iterable[UsageRecord], rates: RateTable | None = None) -> UsageMetrics:
    values = tuple(records)

    def token_sum(field: str) -> int | None:
        counts = [cast(int | None, getattr(record.tokens, field)) for record in values]
        if not counts or any(count is None for count in counts):
            return None
        return sum(count for count in counts if count is not None)

    inputs = token_sum("input_tokens")
    outputs = token_sum("output_tokens")
    costs = [
        amount if amount is not None and type(amount) in (int, float)
        and isfinite(amount) and amount >= 0 else None
        for record in values for amount in (record.reported_cost_usd,)
    ]
    cost_total = sum(amount for amount in costs if amount is not None)
    priced = tuple(price_usage(rates or {}, record) for record in values)

    def known_sum(amounts: Iterable[float | None]) -> float | None:
        known = tuple(amount for amount in amounts if amount is not None)
        result = sum(known)
        return result if known and isfinite(result) else None

    categories = tuple(row.category_cost_usd for row in priced if row.category_cost_usd)
    category_cost: UsageCategoryCost = {
        "input": sum(row["input"] for row in categories),
        "cache_read": sum(row["cache_read"] for row in categories),
        "cache_write": sum(row["cache_write"] for row in categories),
        "output": sum(row["output"] for row in categories),
    }
    category_total = (
        category_cost["input"] + category_cost["cache_read"]
        + category_cost["cache_write"] + category_cost["output"]
    )
    speed_cost = {speed: 0.0 for speed in ("standard", "fast", "ultrafast")}
    for record, row in zip(values, priced, strict=True):
        if row.cost_usd is not None:
            speed_cost[record.speed] += row.cost_usd
    sessions = [
        (record.provider, record.session_id if record.provider == "claude"
         else record.thread_id or record.session_id)
        for record in values
    ]
    return {
        "input_tokens": inputs,
        "uncached_input_tokens": token_sum("uncached_input_tokens"),
        "cached_input_tokens": token_sum("cached_input_tokens"),
        "cache_creation_tokens": token_sum("cache_creation_tokens"),
        "output_tokens": outputs,
        "reasoning_tokens": token_sum("reasoning_tokens"),
        "total_tokens": None if inputs is None or outputs is None else inputs + outputs,
        "reported_cost_usd": (
            cost_total if costs and all(cost is not None for cost in costs)
            and isfinite(cost_total) else None
        ),
        "cost_usd": known_sum(row.cost_usd for row in priced),
        "estimated_cost_usd": known_sum(
            row.cost_usd for row in priced if row.cost_source == "model_priced"
        ),
        "provider_reported_cost_usd": known_sum(
            row.cost_usd for row in priced if row.cost_source == "provider_reported"
        ),
        "category_cost_usd": category_cost if categories and isfinite(category_total) else None,
        "speed_cost_usd": {
            "standard": speed_cost["standard"], "fast": speed_cost["fast"],
            "ultrafast": speed_cost["ultrafast"],
        } if any(row.cost_usd is not None for row in priced)
        and all(isfinite(amount) for amount in speed_cost.values())
        and isfinite(sum(speed_cost.values())) else None,
        "cache_savings_usd": known_sum(row.cache_savings_usd for row in priced),
        "speed_premium_usd": known_sum(row.speed_premium_usd for row in priced),
        "unpriced_records": sum(row.cost_source == "unpriced" for row in priced),
        "model_priced_records": sum(row.cost_source == "model_priced" for row in priced),
        "provider_reported_records": sum(row.cost_source == "provider_reported" for row in priced),
        "record_count": len(values),
        "session_count": (
            len(set(sessions)) if sessions and all(session for _, session in sessions) else None
        ),
        "unknown_records": sum(
            record.tokens.input_tokens is None or record.tokens.output_tokens is None
            for record in values
        ),
        "unknown_cost_records": sum(cost is None for cost in costs),
    }


def provider_metrics(
    records: tuple[UsageRecord, ...], rates: RateTable | None = None
) -> list[ProviderUsageMetrics]:
    return [
        {"provider": provider,
         **metrics((record for record in records if record.provider == provider), rates)}
        for provider in PROVIDERS
    ]


def summarize(
    snapshots: Iterable[SourceSnapshot], *, now: datetime, days: int, timezone: str,
    prices: UsagePriceSnapshot | None = None,
) -> UsageSummary:
    zone = ZoneInfo(timezone)
    local_now = now.astimezone(zone)
    first_date = local_now.date() - timedelta(days=days - 1)
    until = now.astimezone(UTC)
    since = (
        until - timedelta(hours=24) if days == 1 else
        datetime.combine(first_date, time.min, tzinfo=zone).astimezone(UTC)
    )
    sources = tuple(snapshots)
    rates = parse_rate_table(prices.document) if prices is not None else {}
    verified, duplicates, conflicts = verified_records(
        (record for source in sources for record in source.records),
        (thread for source in sources for thread in source.modern_threads),
        ((source.provider, key) for source in sources for key in source.conflicted_record_keys),
    )
    selected = tuple(record for record in verified if since <= record.occurred_at < until)
    models: dict[tuple[UsageProvider, str | None], list[UsageRecord]] = defaultdict(list)
    buckets: dict[datetime, list[UsageRecord]] = defaultdict(list)
    for record in selected:
        models[(record.provider, record.model)].append(record)
        local = record.occurred_at.astimezone(zone)
        if days == 1:
            # Absolute hours do not overlap, including zones with half-hour DST shifts.
            start = record.occurred_at.astimezone(UTC).replace(
                minute=0, second=0, microsecond=0
            )
        else:
            start = datetime.combine(local.date(), time.min, tzinfo=zone).astimezone(UTC)
        buckets[start].append(record)
    periods: list[PeriodUsageMetrics] = []
    for start, bucket in sorted(buckets.items()):
        local = start.astimezone(zone)
        end = (
            start + timedelta(hours=1) if days == 1 else
            datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)
            .astimezone(UTC)
        )
        periods.append({
            "start": timestamp(max(start, since)), "end": timestamp(min(end, until)),
            "label": local.strftime("%H:%M %Z" if days == 1 else "%Y-%m-%d"),
            "providers": provider_metrics(tuple(bucket), rates), **metrics(bucket, rates),
        })
    summaries: list[UsageSourceSummary] = []
    for provider in PROVIDERS:
        source = next((source for source in sources if source.provider == provider), None)
        issues = list(source.issues) if source else ["No local usage read yet."]
        state = source.state if source else "unavailable"
        if provider in conflicts:
            issues.append("Some native identity or session attribution is uncertain.")
            if state == "ready":
                state = "partial"
        summaries.append({
            "provider": provider, "state": state,
            "observed_at": timestamp(source.observed_at) if source else None,
            "files_scanned": source.files_scanned if source else 0,
            "record_count": sum(record.provider == provider for record in selected),
            "issues": list(dict.fromkeys(issues)),
            "duplicates": (source.duplicates if source else 0) + duplicates[provider],
            "progress": source.progress if source else None,
        })
    model_metrics: list[ModelUsageMetrics] = [
        {"provider": provider, "model": model, **metrics(records, rates)}
        for (provider, model), records in sorted(
            models.items(), key=lambda entry: (entry[0][0], entry[0][1] or "")
        )
    ]
    return {
        "scope": "local_cli", "days": days, "timezone": timezone,
        "since": timestamp(since), "until": timestamp(until),
        "observed_at": max((timestamp(source.observed_at) for source in sources), default=None),
        "pricing": pricing_summary(prices, now=now),
        "sources": summaries, "totals": metrics(selected, rates),
        "providers": provider_metrics(selected, rates), "models": model_metrics, "periods": periods,
    }
