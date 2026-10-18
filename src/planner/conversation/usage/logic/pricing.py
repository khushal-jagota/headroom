"""T3 Code's model pricing rules, adapted to Panels' nullable usage counters.

Adapted from T3 Code usagePricing.ts at e58a4857130a93cc58b45dd6ebee14cf08df04ec.
Copyright (c) 2026 T3 Tools Inc. MIT license: THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite

from planner.conversation.usage.contracts import (
    UsageCategoryCost,
    UsageCostSource,
    UsageRecord,
    UsageSpeed,
)


@dataclass(frozen=True, slots=True)
class TokenRates:
    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(frozen=True, slots=True)
class ModelRate:
    standard: TokenRates
    fast: TokenRates | None = None
    ultrafast: TokenRates | None = None


RateTable = Mapping[str, ModelRate]


@dataclass(frozen=True, slots=True)
class PricedUsage:
    cost_usd: float | None
    cost_source: UsageCostSource
    category_cost_usd: UsageCategoryCost | None = None
    speed_premium_usd: float | None = None
    cache_savings_usd: float | None = None


def _number(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        amount = float(value)
    except OverflowError:
        return None
    return amount if isfinite(amount) and amount >= 0 else None


def _rates(
    entry: dict[str, object], suffix: str, standard: TokenRates | None = None
) -> TokenRates | None:
    input_rate = _number(entry.get("input_cost_per_token" + suffix))
    output_rate = _number(entry.get("output_cost_per_token" + suffix))
    if input_rate is None or output_rate is None:
        return None

    def cache_rate(name: str, field: str) -> float:
        explicit = _number(entry.get(name + suffix))
        if explicit is not None:
            return explicit
        if standard is not None and standard.input > 0:
            return float(getattr(standard, field)) / standard.input * input_rate
        return input_rate

    return TokenRates(
        input_rate, output_rate,
        cache_rate("cache_read_input_token_cost", "cache_read"),
        cache_rate("cache_creation_input_token_cost", "cache_write"),
    )


def _scale(rates: TokenRates, multiple: float) -> TokenRates:
    return TokenRates(*(rate * multiple for rate in (
        rates.input, rates.output, rates.cache_read, rates.cache_write
    )))


def parse_rate_table(document: object) -> dict[str, ModelRate]:
    """Keep complete rates and only unambiguous bare aliases, as T3 does."""
    table: dict[str, ModelRate] = {}
    if not isinstance(document, dict):
        return table
    for name, entry in document.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        standard = _rates(entry, "")
        key = name.strip().lower()
        if standard is None or not key:
            continue
        specific = entry.get("provider_specific_entry")
        multiple = _number(specific.get("fast")) if isinstance(specific, dict) else None
        table[key] = ModelRate(
            standard,
            _scale(standard, multiple) if multiple is not None and multiple > 0
            else _rates(entry, "_priority", standard),
            _rates(entry, "_ultrafast", standard),
        )
    aliases: dict[str, ModelRate | None] = {}
    for key, rate in table.items():
        alias = key.rsplit("/", 1)[-1]
        if not alias or alias == key or alias in table:
            continue
        if alias not in aliases:
            aliases[alias] = rate
        elif aliases[alias] != rate:
            aliases[alias] = None
    table.update((alias, rate) for alias, rate in aliases.items() if rate is not None)
    return table


_UNPRICEABLE = frozenset({
    "<synthetic>", "synthetic", "opus", "sonnet", "haiku", "fable",
})


def lookup_rate(table: RateTable, model: str | None) -> ModelRate | None:
    key = (model or "").strip().lower().split("[", 1)[0]
    bare = key.rsplit("/", 1)[-1]
    return None if not bare or bare in _UNPRICEABLE else table.get(key)


def _at_speed(rate: ModelRate, speed: UsageSpeed) -> TokenRates:
    return (None if speed == "standard" else getattr(rate, speed)) or rate.standard


def _category(record: UsageRecord, rates: TokenRates) -> UsageCategoryCost | None:
    tokens = record.tokens
    values = (
        tokens.uncached_input_tokens, tokens.cached_input_tokens,
        tokens.cache_creation_tokens, tokens.output_tokens,
    )
    if any(value is None for value in values):
        return None
    # Reasoning is already part of output, as in the reference implementation.
    try:
        result: UsageCategoryCost = {
            "input": (tokens.uncached_input_tokens or 0) * rates.input,
            "cache_read": (tokens.cached_input_tokens or 0) * rates.cache_read,
            "cache_write": (tokens.cache_creation_tokens or 0) * rates.cache_write,
            "output": (tokens.output_tokens or 0) * rates.output,
        }
    except OverflowError:
        return None
    return result if isfinite(_sum_categories(result)) else None


def _sum_categories(cost: UsageCategoryCost) -> float:
    return cost["input"] + cost["cache_read"] + cost["cache_write"] + cost["output"]


def price_usage(table: RateTable, record: UsageRecord) -> PricedUsage:
    """Prefer reported USD; otherwise use the exact table rate or leave unpriced."""
    reported = _number(record.reported_cost_usd)
    source: UsageCostSource = "provider_reported" if reported is not None else "unpriced"
    rate = lookup_rate(table, record.model)
    if rate is None:
        return PricedUsage(reported, source)
    rates = _at_speed(rate, record.speed)
    category = _category(record, rates)
    try:
        savings = (
            record.tokens.cached_input_tokens * (rates.input - rates.cache_read)
            if record.tokens.cached_input_tokens is not None else None
        )
    except OverflowError:
        savings = None
    if savings is not None and not isfinite(savings):
        savings = None
    if category is None:
        return PricedUsage(reported, source, cache_savings_usd=savings)
    list_cost = _sum_categories(category)
    if not isfinite(list_cost):
        return PricedUsage(reported, source, cache_savings_usd=savings)
    if reported is not None and list_cost <= 0:
        return PricedUsage(reported, source, cache_savings_usd=savings)
    standard = _category(record, rate.standard)
    premium: float | None = 0.0
    if record.speed != "standard":
        premium = list_cost - _sum_categories(standard) if standard is not None else None
    # Scale bounded category shares, not a potentially infinite reported/list ratio.
    split = category if reported is None else UsageCategoryCost(
        input=reported * (category["input"] / list_cost),
        cache_read=reported * (category["cache_read"] / list_cost),
        cache_write=reported * (category["cache_write"] / list_cost),
        output=reported * (category["output"] / list_cost),
    )
    finite_split = split if isfinite(_sum_categories(split)) else None
    if premium is not None and reported is not None:
        premium = reported * (premium / list_cost)
    if premium is not None and not isfinite(premium):
        premium = None
    return PricedUsage(
        list_cost if reported is None else reported,
        "model_priced" if reported is None else "provider_reported",
        finite_split, premium, savings,
    )
