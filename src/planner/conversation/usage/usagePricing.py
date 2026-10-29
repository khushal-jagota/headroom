"""T3 usagePricing.ts translated from revision 75e4ceb96489.

Copyright (c) 2026 T3 Tools Inc. MIT. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from typing import Any

from .usageTranscripts import finiteNumber

RateTable = dict[str, dict[str, float]]
UNPRICEABLE_MODELS = {"<synthetic>", "synthetic", "opus", "sonnet", "haiku", "fable"}


def createOverrideRateTable(overrides: dict[str, Any]) -> RateTable:
    return {
        model.strip(): dict(
            inputCostPerToken=prices["inputCostPerMillionTokens"] / 1_000_000,
            outputCostPerToken=prices["outputCostPerMillionTokens"] / 1_000_000,
            cacheReadCostPerToken=(
                prices.get("cacheReadCostPerMillionTokens")
                if prices.get("cacheReadCostPerMillionTokens") is not None
                else prices["inputCostPerMillionTokens"]
            )
            / 1_000_000,
            cacheCreationCostPerToken=(
                prices.get("cacheWriteCostPerMillionTokens")
                if prices.get("cacheWriteCostPerMillionTokens") is not None
                else prices["inputCostPerMillionTokens"]
            )
            / 1_000_000,
        )
        for model, prices in overrides.items()
    }


def parseRateTable(document: Any) -> RateTable:
    table: RateTable = {}
    if not isinstance(document, dict):
        return table
    for name, raw in document.items():
        if not isinstance(raw, dict):
            continue
        inputRate = raw.get("input_cost_per_token")
        outputRate = raw.get("output_cost_per_token")
        if not finiteNumber(inputRate) or not finiteNumber(outputRate):
            continue
        key = normalizeRateKey(name)
        if not key:
            continue
        table[key] = dict(
            inputCostPerToken=inputRate,
            outputCostPerToken=outputRate,
            cacheReadCostPerToken=raw["cache_read_input_token_cost"]
            if finiteNumber(raw.get("cache_read_input_token_cost"))
            else inputRate,
            cacheCreationCostPerToken=raw["cache_creation_input_token_cost"]
            if finiteNumber(raw.get("cache_creation_input_token_cost"))
            else inputRate,
        )
    aliasCandidates: dict[str, dict[str, float] | None] = {}
    for key, rate in table.items():
        alias = bareModelName(key)
        if not alias or alias == key or alias in table:
            continue
        if alias not in aliasCandidates:
            aliasCandidates[alias] = rate
        elif aliasCandidates[alias] is not None and aliasCandidates[alias] != rate:
            aliasCandidates[alias] = None
    for alias, aliasRate in aliasCandidates.items():
        if aliasRate is not None:
            table[alias] = aliasRate
    return table


def normalizeRateKey(model: str) -> str:
    return model.strip().lower()


def bareModelName(key: str) -> str:
    return key.rsplit("/", 1)[-1]


def stripVariantSuffix(key: str) -> str:
    return key.split("[", 1)[0]


def lookupRate(table: RateTable, model: str) -> dict[str, float] | None:
    key = stripVariantSuffix(normalizeRateKey(model))
    bareName = bareModelName(key)
    if not bareName or bareName in UNPRICEABLE_MODELS:
        return None
    return table.get(key)


def priceUsage(
    table: RateTable,
    model: str,
    totals: dict[str, Any],
    reportedCostUsd: float | None,
    overrides: RateTable | None = None,
) -> dict[str, Any]:
    override = (overrides or {}).get(model.strip())
    if override is None and reportedCostUsd is not None and finiteNumber(reportedCostUsd):
        return dict(costUsd=reportedCostUsd, costSource="providerReported")
    rate = override if override is not None else lookupRate(table, model)
    if rate is None:
        return dict(costUsd=0, costSource="unpriced")
    costUsd = (
        totals["uncachedInputTokens"] * rate["inputCostPerToken"]
        + totals["cachedInputTokens"] * rate["cacheReadCostPerToken"]
        + totals["cacheCreationTokens"] * rate["cacheCreationCostPerToken"]
        + totals["outputTokens"] * rate["outputCostPerToken"]
    )
    return dict(costUsd=costUsd, costSource="modelPriced")


def cacheSavingsUsd(
    table: RateTable, model: str, totals: dict[str, Any], overrides: RateTable | None = None
) -> float:
    rate = (overrides or {}).get(model.strip()) or lookupRate(table, model)
    if rate is None:
        return 0
    return float(
        totals["cachedInputTokens"] * (rate["inputCostPerToken"] - rate["cacheReadCostPerToken"])
    )
