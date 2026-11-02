"""All T3 usagePricing.test.ts cases, revision 75e4ceb9 (MIT)."""

from typing import Any, cast

from planner.conversation.usage.usagePricing import (
    cacheSavingsUsd,
    createOverrideRateTable,
    lookupRate,
    parseRateTable,
    priceUsage,
)

TOTALS = dict(
    uncachedInputTokens=1000000,
    cachedInputTokens=1000000,
    cacheCreationTokens=1000000,
    outputTokens=1000000,
    reasoningTokens=500000,
)


def rate(input: float, cacheRead: float | None = None) -> dict[str, Any]:
    return dict(input_cost_per_token=input, output_cost_per_token=input * 5) | (
        {} if cacheRead is None else dict(cache_read_input_token_cost=cacheRead)
    )


def test_uses_custom_token_rates_ahead_of_public_and_provider_reported_costs() -> None:
    """uses custom token rates ahead of public and provider-reported costs"""
    table = parseRateTable({"example-model": rate(1)})
    overrides = createOverrideRateTable(
        {
            "example-model": dict(
                inputCostPerMillionTokens=2,
                outputCostPerMillionTokens=8,
                cacheReadCostPerMillionTokens=0.5,
                cacheWriteCostPerMillionTokens=3,
            )
        }
    )
    for reportedCostUsd in (None, 99):
        assert priceUsage(table, "example-model", TOTALS, reportedCostUsd, overrides) == dict(
            costUsd=13.5, costSource="modelPriced"
        )
    assert cacheSavingsUsd(table, "example-model", TOTALS, overrides) == 1.5


def test_prices_unknown_models_offline_and_uses_input_prices_for_omitted_cache_rates() -> None:
    """prices unknown models offline and uses input prices for omitted cache rates"""
    table = parseRateTable({})
    overrides = createOverrideRateTable(
        {"example-model": dict(inputCostPerMillionTokens=2, outputCostPerMillionTokens=8)}
    )
    assert priceUsage(table, "example-model", TOTALS, None, overrides) == dict(
        costUsd=14, costSource="modelPriced"
    )
    assert cacheSavingsUsd(table, "example-model", TOTALS, overrides) == 0


def test_preserves_explicit_zero_rates_and_matches_only_the_exact_trimmed_model_ID() -> None:
    """preserves explicit zero rates and matches only the exact trimmed model ID"""
    table = parseRateTable({})
    overrides = createOverrideRateTable(
        {
            " vendor/example-model[1m] ": dict(
                inputCostPerMillionTokens=0, outputCostPerMillionTokens=0
            )
        }
    )
    assert priceUsage(table, " vendor/example-model[1m] ", TOTALS, 99, overrides) == dict(
        costUsd=0, costSource="modelPriced"
    )
    for model in (
        "example-model[1m]",
        "vendor/example-model",
        "vendor/Example-model[1m]",
        "other/example-model[1m]",
    ):
        assert priceUsage(table, model, TOTALS, None, overrides)["costSource"] == "unpriced"
        assert priceUsage(table, model, TOTALS, 99, overrides) == dict(
            costUsd=99, costSource="providerReported"
        )


def test_keeps_the_canonical_Fable_rate_separate_from_DeepInfra_in_either_order() -> None:
    """keeps the canonical Fable rate separate from DeepInfra in either order"""
    canonical = ("claude-fable-5", rate(1e-5, 1e-6))
    deepInfra = ("deepinfra/anthropic/claude-fable-5", rate(1e-5))
    for entries in ((canonical, deepInfra), (deepInfra, canonical)):
        table = parseRateTable(dict(entries))
        assert (
            cast(dict[str, float], lookupRate(table, "claude-fable-5"))["cacheReadCostPerToken"]
            == 1e-6
        )
        assert (
            cast(dict[str, float], lookupRate(table, "deepinfra/anthropic/claude-fable-5"))[
                "cacheReadCostPerToken"
            ]
            == 1e-5
        )
        assert lookupRate(table, "other/claude-fable-5") is None


def test_prices_a_bracketed_context_tier_variant_at_the_base_models_rate() -> None:
    """prices a bracketed context-tier variant at the base model's rate"""
    table = parseRateTable({"claude-fable-5-1": rate(1e-5, 2.5e-7)})
    assert lookupRate(table, "claude-fable-5-1[1m]") == lookupRate(table, "claude-fable-5-1")
    assert lookupRate(table, "anthropic/Claude-Fable-5-1[1m]") is None


def test_adds_a_bare_alias_when_every_qualified_entry_has_the_same_rate() -> None:
    """adds a bare alias when every qualified entry has the same rate"""
    table = parseRateTable(
        {"provider-a/example-model": rate(1), "provider-b/example-model": rate(1)}
    )
    assert lookupRate(table, "example-model") == lookupRate(table, "provider-a/example-model")


def test_leaves_an_ambiguous_bare_name_unpriced() -> None:
    """leaves an ambiguous bare name unpriced"""
    table = parseRateTable(
        {"provider-a/example-model": rate(1), "provider-b/example-model": rate(3)}
    )
    assert (
        cast(dict[str, float], lookupRate(table, "provider-a/example-model"))["inputCostPerToken"]
        == 1
    )
    assert (
        cast(dict[str, float], lookupRate(table, "provider-b/example-model"))["inputCostPerToken"]
        == 3
    )
    assert lookupRate(table, "example-model") is None
