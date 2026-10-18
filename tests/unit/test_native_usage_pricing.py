"""T3 price-table parity, nullable counters, and owned offline snapshot behavior."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from math import isfinite
from sqlite3 import Connection

import httpx
import pytest
from tests.unit.test_recorded_usage import NOW, record

from planner.conversation.usage.adapters import prices
from planner.conversation.usage.contracts import (
    SourceSnapshot,
    UsagePriceSnapshot,
    UsageRecord,
    UsageTokens,
)
from planner.conversation.usage.logic.pricing import lookup_rate, parse_rate_table, price_usage
from planner.conversation.usage.logic.summary import metrics, summarize
from planner.conversation.usage.pricing import (
    UsagePricingService,
    pricing_summary,
    read_price_snapshot,
)
from planner.core.db import connect


def rates(input_rate: float = 4e-6, cache_read: float = 4e-7) -> dict[str, object]:
    return {
        "input_cost_per_token": input_rate, "output_cost_per_token": input_rate * 5,
        "cache_read_input_token_cost": cache_read,
        "cache_creation_input_token_cost": 5e-6,
    }


def priced_record() -> UsageRecord:
    return replace(
        record(), model="example-model", tokens=UsageTokens(
            3_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000, 500_000
        ),
    )


def test_t3_categories_cache_savings_and_reasoning_subset() -> None:
    table = parse_rate_table({"example-model": rates()})
    result = price_usage(table, priced_record())
    assert result.cost_source == "model_priced"
    assert result.cost_usd == pytest.approx(29.4)
    assert result.category_cost_usd == pytest.approx({
        "input": 4.0, "cache_read": 0.4, "cache_write": 5.0, "output": 20.0,
    })
    assert result.cache_savings_usd == pytest.approx(3.6)
    assert result.speed_premium_usd == 0


def test_t3_fast_multiple_and_reported_total_scale_categories() -> None:
    table = parse_rate_table({
        "example-model": {**rates(), "provider_specific_entry": {"fast": 2}},
    })
    fast = replace(priced_record(), speed="fast")
    result = price_usage(table, fast)
    assert result.cost_usd == pytest.approx(58.8)
    assert result.speed_premium_usd == pytest.approx(29.4)
    assert result.cache_savings_usd == pytest.approx(7.2)
    reported = price_usage(table, replace(fast, reported_cost_usd=29.4))
    assert reported.cost_source == "provider_reported"
    assert reported.cost_usd == 29.4
    assert reported.speed_premium_usd == pytest.approx(14.7)
    assert reported.category_cost_usd == pytest.approx({
        "input": 4.0, "cache_read": 0.4, "cache_write": 5.0, "output": 20.0,
    })


def test_t3_priority_ultrafast_and_cache_ratio_fallback() -> None:
    table = parse_rate_table({"example-model": {
        **rates(), "input_cost_per_token_priority": 8e-6,
        "output_cost_per_token_priority": 40e-6,
        "input_cost_per_token_ultrafast": 24e-6,
        "output_cost_per_token_ultrafast": 120e-6,
    }})
    standard = price_usage(table, priced_record())
    assert price_usage(table, replace(priced_record(), speed="fast")).cost_usd == pytest.approx(
        (standard.cost_usd or 0) * 2
    )
    assert price_usage(table, replace(priced_record(), speed="ultrafast")).cost_usd \
        == pytest.approx((standard.cost_usd or 0) * 6)
    no_tier = parse_rate_table({"example-model": rates()})
    assert price_usage(no_tier, replace(priced_record(), speed="fast")).cost_usd \
        == standard.cost_usd


def test_t3_aliases_preserve_canonical_and_reject_ambiguous_or_family_names() -> None:
    table = parse_rate_table({
        "example-model": rates(), "other/example-model": rates(8e-6),
        "provider-a/shared": rates(), "provider-b/shared": rates(),
        "provider-a/ambiguous": rates(), "provider-b/ambiguous": rates(8e-6),
        "sonnet": rates(), "<synthetic>": rates(),
    })
    assert lookup_rate(table, " Example-Model[1m] ") == table["example-model"]
    assert lookup_rate(table, "shared") == table["provider-a/shared"]
    for model in ("ambiguous", "unlisted/example-model", "sonnet", "<synthetic>", ""):
        assert lookup_rate(table, model) is None
    assert table["example-model"] != table["other/example-model"]


def test_half_rates_invalid_rates_and_missing_categories_are_not_invented() -> None:
    table = parse_rate_table({
        "half": {"input_cost_per_token": 1},
        "negative": {"input_cost_per_token": -1, "output_cost_per_token": 2},
        "bool": {"input_cost_per_token": True, "output_cost_per_token": 2},
        "example-model": rates(),
    })
    assert set(table) == {"example-model"}
    incomplete = replace(
        priced_record(), tokens=replace(priced_record().tokens, cached_input_tokens=None)
    )
    assert price_usage(table, incomplete).cost_source == "unpriced"
    assert price_usage(table, incomplete).cost_usd is None
    reported = price_usage(table, replace(incomplete, reported_cost_usd=2.5))
    assert reported.cost_usd == 2.5
    assert reported.category_cost_usd is None


def test_explicit_zero_and_absent_cache_rates_keep_t3_semantics() -> None:
    table = parse_rate_table({"example-model": {
        "input_cost_per_token": 0, "output_cost_per_token": 0,
    }})
    assert price_usage(table, priced_record()).cost_usd == 0
    assert price_usage(table, replace(priced_record(), reported_cost_usd=7)).cost_usd == 7
    plain = parse_rate_table({"example-model": {
        "input_cost_per_token": 2e-6, "output_cost_per_token": 8e-6,
    }})
    assert price_usage(plain, priced_record()).cost_usd == 14
    assert price_usage(plain, priced_record()).cache_savings_usd == 0


@pytest.mark.parametrize("tokens", [
    UsageTokens(10**400, 10**400, 1, 0, 0, 0),
    UsageTokens(10**400, 0, 1, 10**400, 0, 0),
])
def test_oversized_counters_stay_unpriced_without_overflow_and_keep_reported_usd(
    tokens: UsageTokens,
) -> None:
    table = parse_rate_table({"example-model": rates(1e-6, 1e-7)})
    corrupt = replace(priced_record(), tokens=tokens)
    unpriced = price_usage(table, corrupt)
    assert unpriced.cost_usd is None
    assert unpriced.cost_source == "unpriced"
    assert unpriced.category_cost_usd is None
    assert metrics((corrupt,), table)["unpriced_records"] == 1
    reported = price_usage(table, replace(corrupt, reported_cost_usd=7))
    assert reported.cost_usd == 7
    assert reported.cost_source == "provider_reported"
    assert reported.category_cost_usd is None
    assert reported.speed_premium_usd is None
    assert reported.cache_savings_usd is None or isfinite(reported.cache_savings_usd)


def test_extreme_reported_scaling_uses_finite_category_shares() -> None:
    table = parse_rate_table({"example-model": {
        "input_cost_per_token": 1e-308, "output_cost_per_token": 1e-308,
    }})
    tiny = replace(priced_record(), tokens=UsageTokens(1, 1, 1, 0, 0, 0),
                   reported_cost_usd=1e308)
    result = price_usage(table, tiny)
    assert result.cost_usd == 1e308
    assert result.cost_source == "provider_reported"
    assert result.category_cost_usd == {
        "input": 5e307, "cache_read": 0.0, "cache_write": 0.0, "output": 5e307,
    }
    assert result.speed_premium_usd == 0
    assert result.cache_savings_usd == 0


def test_unknown_standard_split_does_not_create_a_false_speed_premium() -> None:
    table = parse_rate_table({"example-model": {
        "input_cost_per_token": 1e308, "output_cost_per_token": 1e308,
        "input_cost_per_token_priority": 1e-308,
        "output_cost_per_token_priority": 1e-308,
    }})
    native = replace(priced_record(), tokens=UsageTokens(1, 1, 1, 0, 0, 0),
                     reported_cost_usd=1e308, speed="fast")
    result = price_usage(table, native)
    assert result.cost_usd == 1e308
    assert result.category_cost_usd == {
        "input": 5e307, "cache_read": 0.0, "cache_write": 0.0, "output": 5e307,
    }
    assert result.speed_premium_usd is None


def test_partial_known_cost_keeps_reported_and_model_subtotals_separate() -> None:
    table = parse_rate_table({"example-model": rates()})
    native = priced_record()
    reported = replace(native, record_key="reported", reported_cost_usd=5)
    unpriced = replace(native, record_key="unknown", model="unlisted")
    result = metrics((native, reported, unpriced), table)
    assert result["cost_usd"] == pytest.approx(34.4)
    assert result["estimated_cost_usd"] == pytest.approx(29.4)
    assert result["provider_reported_cost_usd"] == 5
    assert result["reported_cost_usd"] is None
    assert result["unknown_cost_records"] == 2
    assert result["unpriced_records"] == 1
    assert result["model_priced_records"] == 1
    assert result["provider_reported_records"] == 1
    assert metrics((unpriced,), table)["cost_usd"] is None
    assert metrics((), table)["cost_usd"] is None


def test_aggregate_overflow_stays_unknown_and_summary_remains_valid_json() -> None:
    document: dict[str, object] = {"example-model": {
        "input_cost_per_token": 1e-6, "output_cost_per_token": 1e-6,
    }}
    records = tuple(replace(
        priced_record(), record_key=f"codex:response:large-{index}",
        tokens=UsageTokens(1, 1, 1, 0, 0, 0), reported_cost_usd=1e308,
    ) for index in range(4))
    summary = summarize(
        (SourceSnapshot("codex", "local", "ready", NOW, records=records),),
        now=NOW + timedelta(seconds=1), days=7, timezone="UTC",
        prices=UsagePriceSnapshot(prices.LITELLM_RATES_URL, NOW, document),
    )
    for result in (summary["totals"], summary["models"][0], summary["periods"][0]):
        assert result["cost_usd"] is None
        assert result["reported_cost_usd"] is None
        assert result["provider_reported_cost_usd"] is None
        assert result["category_cost_usd"] is None
        assert result["speed_cost_usd"] is None
        assert result["provider_reported_records"] == 4
        assert result["cache_savings_usd"] == 0
        assert result["speed_premium_usd"] == 0
    json.dumps(summary, allow_nan=False)


def test_summary_prices_from_owned_snapshot_and_retains_price_metadata() -> None:
    snapshot = UsagePriceSnapshot(prices.LITELLM_RATES_URL, NOW, {"example-model": rates()})
    source = SourceSnapshot("codex", "local", "ready", NOW, records=(priced_record(),))
    summary = summarize((source,), now=NOW + timedelta(seconds=1), days=7, timezone="UTC",
                        prices=snapshot)
    assert summary["totals"]["cost_usd"] == pytest.approx(29.4)
    assert summary["models"][0]["cost_usd"] == pytest.approx(29.4)
    assert summary["periods"][0]["cost_usd"] == pytest.approx(29.4)
    assert summary["pricing"]["status"] == "fresh"
    assert summary["pricing"]["known_models"] == 1
    assert pricing_summary(snapshot, now=NOW + timedelta(days=2))["status"] == "cached"


def test_price_snapshot_survives_restart_ttl_and_failed_refresh(tmp_db: Connection) -> None:
    database = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    current = [NOW]
    calls: list[int] = []
    document: dict[str, object] = {"example-model": {**rates(), "extra_metadata": "retained"}}

    def fetch() -> dict[str, object]:
        calls.append(1)
        return document

    service = UsagePricingService(lambda: connect(database), now=lambda: current[0], fetch=fetch)
    assert service.refresh()["status"] == "fresh"
    assert service.refresh(force=True)["status"] == "fresh"
    assert len(calls) == 1
    current[0] += timedelta(seconds=61)
    service.refresh()
    assert len(calls) == 1  # T3 ordinary 24-hour TTL.
    service.refresh(force=True)
    assert len(calls) == 2
    with connect(database) as conn:
        stored = read_price_snapshot(conn)
    assert stored is not None and stored.document == document
    assert stored.fetched_at == current[0]

    def unavailable() -> dict[str, object]:
        raise OSError("offline")

    current[0] += timedelta(days=2)
    restarted = UsagePricingService(
        lambda: connect(database), now=lambda: current[0], fetch=unavailable
    )
    status = restarted.refresh()
    assert status["status"] == "cached"
    with connect(database) as conn:
        assert read_price_snapshot(conn) == stored


@pytest.mark.parametrize("body", [b"[]", b"x" * 33])
def test_external_price_adapter_bounds_decoded_bytes_and_rejects_invalid_shape(
    monkeypatch: pytest.MonkeyPatch, body: bytes,
) -> None:
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body))
    monkeypatch.setattr(prices, "MAX_RATES_BYTES", 32)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(
        **kwargs, transport=transport
    ))
    with pytest.raises(ValueError):
        prices.fetch_rate_document()
