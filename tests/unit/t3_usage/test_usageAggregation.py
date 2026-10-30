"""All T3 usageAggregation.test.ts cases, revision 75e4ceb9 (MIT)."""

from typing import Any

import pytest

from planner.conversation.usage.usageAggregation import UsageAggregator
from planner.conversation.usage.usageTranscripts import parseTimestampMs

RATES = {
    "claude-fable-5": dict(
        inputCostPerToken=1e-5,
        outputCostPerToken=5e-5,
        cacheReadCostPerToken=1e-6,
        cacheCreationCostPerToken=1.25e-5,
    )
}


def record(**overrides: Any) -> dict[str, Any]:
    return (
        dict(
            provider="claude",
            timestampMs=parseTimestampMs("2026-08-07T04:05:13.944Z"),
            model="claude-fable-5",
            sessionId="session-a",
            totals=dict(
                uncachedInputTokens=100,
                cachedInputTokens=1000,
                cacheCreationTokens=10,
                outputTokens=50,
                reasoningTokens=0,
            ),
            reportedCostUsd=None,
            dedupeKey=None,
        )
        | overrides
    )


def aggregate(
    records: list[dict[str, Any]], timeZone: str = "UTC", resolution: str = "day"
) -> dict[str, Any]:
    bounds = (
        dict(
            sinceTimeMs=parseTimestampMs("2026-08-06T04:37:00.000Z"),
            untilTimeMs=parseTimestampMs("2026-08-07T04:37:00.000Z"),
        )
        if resolution == "hour"
        else {}
    )
    aggregator = UsageAggregator(
        dict(
            timeZone=timeZone,
            sinceDay="2026-08-01",
            untilDay="2026-08-31",
            resolution=resolution,
            rates=RATES,
            **bounds,
        )
    )
    for item in records:
        aggregator.add(item)
    return aggregator.finish()


def test_requires_exact_bounds_for_hourly_aggregation() -> None:
    """requires exact bounds for hourly aggregation"""
    with pytest.raises(ValueError, match="requires exact time bounds"):
        UsageAggregator(
            dict(
                timeZone="UTC",
                sinceDay="2026-08-01",
                untilDay="2026-08-31",
                resolution="hour",
                rates=RATES,
            )
        )


def test_keeps_only_the_first_record_for_a_repeated_dedupe_key() -> None:
    """keeps only the first record for a repeated dedupe key"""
    result = aggregate([record(dedupeKey="msg_1:") for _ in range(3)])
    assert result["duplicatesDropped"] == 2
    assert len(result["buckets"]) == 1
    assert result["buckets"][0]["records"] == 1
    assert result["buckets"][0]["totals"]["outputTokens"] == 50


def test_still_sums_records_that_carry_no_dedupe_key() -> None:
    """still sums records that carry no dedupe key"""
    result = aggregate([record(), record()])
    assert result["duplicatesDropped"] == 0
    assert result["buckets"][0]["totals"]["outputTokens"] == 100


def test_buckets_by_the_day_in_the_requested_time_zone() -> None:
    """buckets by the day in the requested time zone"""
    assert aggregate([record()], "UTC")["buckets"][0]["day"] == "2026-08-07"
    assert aggregate([record()], "America/Los_Angeles")["buckets"][0]["day"] == "2026-08-06"


def test_splits_an_hourly_request_into_fixed_buckets_anchored_to_its_exact_start() -> None:
    """splits an hourly request into fixed buckets anchored to its exact start"""
    result = aggregate(
        [
            record(timestampMs=parseTimestampMs(time))
            for time in ("2026-08-07T02:40:13.944Z", "2026-08-07T03:40:13.944Z")
        ],
        "America/Los_Angeles",
        "hour",
    )
    assert [[bucket["day"], bucket["hourStart"]] for bucket in result["buckets"]] == [
        ["2026-08-06", "2026-08-07T02:37:00.000Z"],
        ["2026-08-06", "2026-08-07T03:37:00.000Z"],
    ]


def test_uses_an_inclusive_start_and_exclusive_end_for_rolling_windows() -> None:
    """uses an inclusive start and exclusive end for rolling windows"""
    result = aggregate(
        [
            record(timestampMs=parseTimestampMs(time))
            for time in (
                "2026-08-06T04:36:59.999Z",
                "2026-08-06T04:37:00.000Z",
                "2026-08-07T04:36:59.999Z",
                "2026-08-07T04:37:00.000Z",
            )
        ],
        "UTC",
        "hour",
    )
    assert result["outOfWindow"] == 2
    assert [bucket["hourStart"] for bucket in result["buckets"]] == [
        "2026-08-06T04:37:00.000Z",
        "2026-08-07T03:37:00.000Z",
    ]


def test_keeps_daily_payloads_collapsed_when_hourly_resolution_is_not_requested() -> None:
    """keeps daily payloads collapsed when hourly resolution is not requested"""
    result = aggregate(
        [
            record(timestampMs=parseTimestampMs(time))
            for time in ("2026-08-07T04:05:13.944Z", "2026-08-07T05:05:13.944Z")
        ]
    )
    assert len(result["buckets"]) == 1
    assert "hourStart" not in result["buckets"][0]
    assert result["buckets"][0]["records"] == 2


def test_prices_against_the_rate_table() -> None:
    """prices against the rate table"""
    result = aggregate([record()])
    assert result["buckets"][0]["costUsd"] == pytest.approx(0.004625, abs=0.5e-9, rel=0)
    assert result["buckets"][0]["costSource"] == "modelPriced"


def test_counts_tokens_but_not_cost_for_a_model_with_no_rate() -> None:
    """counts tokens but not cost for a model with no rate"""
    result = aggregate([record(model="kimi-k3")])
    assert result["buckets"][0]["costUsd"] == 0
    assert result["buckets"][0]["costSource"] == "unpriced"
    assert result["buckets"][0]["unpricedRecords"] == 1
    assert result["buckets"][0]["totals"]["outputTokens"] == 50


def test_prefers_a_reported_cost_over_the_rate_table() -> None:
    """prefers a reported cost over the rate table"""
    result = aggregate([record(reportedCostUsd=1.25)])
    assert result["buckets"][0]["costUsd"] == 1.25
    assert result["buckets"][0]["costSource"] == "providerReported"


def test_drops_records_outside_the_window() -> None:
    """drops records outside the window"""
    result = aggregate([record(timestampMs=parseTimestampMs("2026-07-01T12:00:00Z"))])
    assert result["outOfWindow"] == 1
    assert len(result["buckets"]) == 0


def test_reports_whether_a_record_contributed() -> None:
    """reports whether a record contributed"""
    aggregator = UsageAggregator(
        dict(timeZone="UTC", sinceDay="2026-08-01", untilDay="2026-08-31", rates=RATES)
    )
    assert aggregator.add(record(dedupeKey="msg_1:")) is True
    assert aggregator.add(record(dedupeKey="msg_1:")) is False
    assert aggregator.add(record(timestampMs=parseTimestampMs("2026-07-01T12:00:00Z"))) is False


def test_separates_providers_and_models_into_their_own_buckets() -> None:
    """separates providers and models into their own buckets"""
    result = aggregate(
        [record(), record(provider="codex", model="gpt-5.6-sol"), record(model="claude-opus-5")]
    )
    assert len(result["buckets"]) == 3
