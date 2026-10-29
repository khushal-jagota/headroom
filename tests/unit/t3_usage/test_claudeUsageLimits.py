"""Every upstream claudeUsageLimits.test.ts case, unchanged expected values."""

from planner.conversation.usage.claudeUsageLimits import (
    claudeRateLimitEventToUpdate,
    claudeUsageResponseToLimits,
)

checkedAt = "2026-07-18T10:00:00.000Z"
noNames = {"overageIncluded": None}


def test_maps_the_session_weekly_and_model_scoped_weekly_windows() -> None:
    assert claudeUsageResponseToLimits(
        {
            "checkedAt": checkedAt,
            "response": {
                "rate_limits_available": True,
                "rate_limits": {
                    "five_hour": {"utilization": 54, "resets_at": "2026-07-18T14:39:00Z"},
                    "seven_day": {"utilization": 18.4, "resets_at": "2026-07-24T08:59:00+00:00"},
                    "seven_day_opus": {"utilization": 3, "resets_at": None},
                    "model_scoped": [
                        {
                            "display_name": "Fable",
                            "utilization": 73,
                            "resets_at": "2026-07-24T08:59:00Z",
                        },
                        {"display_name": "Ghost", "utilization": None, "resets_at": None},
                    ],
                    "extra_usage": {
                        "is_enabled": False,
                        "monthly_limit": None,
                        "used_credits": None,
                        "utilization": None,
                    },
                },
            },
        }
    ) == {
        "names": {"overageIncluded": "Fable"},
        "limits": {
            "checkedAt": checkedAt,
            "windows": [
                {
                    "id": "five_hour",
                    "kind": "session",
                    "label": "Session",
                    "usedPercent": 54,
                    "windowDurationMins": 300,
                    "resetsAt": "2026-07-18T14:39:00.000Z",
                },
                {
                    "id": "seven_day",
                    "kind": "weekly",
                    "label": "Weekly",
                    "usedPercent": 18.4,
                    "windowDurationMins": 10080,
                    "resetsAt": "2026-07-24T08:59:00.000Z",
                },
                {
                    "id": "seven_day_fable",
                    "kind": "weekly",
                    "label": "Weekly · Fable",
                    "usedPercent": 73,
                    "windowDurationMins": 10080,
                    "resetsAt": "2026-07-24T08:59:00.000Z",
                },
            ],
        },
    }


def test_names_the_overage_included_bucket_only_from_a_scoped_entry_that_drew_a_row() -> None:
    assert claudeUsageResponseToLimits(
        {
            "checkedAt": checkedAt,
            "response": {
                "rate_limits_available": True,
                "rate_limits": {
                    "model_scoped": [
                        {"display_name": "Ghost", "utilization": None, "resets_at": None},
                        {"display_name": "Fable", "utilization": 5, "resets_at": None},
                    ]
                },
            },
        }
    )["names"] == {"overageIncluded": "Fable"}


def test_reports_api_key_and_bedrock_accounts_as_unsupported() -> None:
    assert claudeUsageResponseToLimits(
        {"checkedAt": checkedAt, "response": {"rate_limits_available": False, "rate_limits": None}}
    )["limits"] == {"checkedAt": checkedAt, "windows": [], "unavailable": {"reason": "unsupported"}}


def test_skips_a_window_the_endpoint_reports_without_a_utilization() -> None:
    assert claudeUsageResponseToLimits(
        {
            "checkedAt": checkedAt,
            "response": {
                "rate_limits_available": True,
                "rate_limits": {
                    "five_hour": {"utilization": None, "resets_at": None},
                    "seven_day": {"utilization": 250, "resets_at": None},
                },
            },
        }
    )["limits"]["windows"] == [
        {
            "id": "seven_day",
            "kind": "weekly",
            "label": "Weekly",
            "usedPercent": 100,
            "windowDurationMins": 10080,
        }
    ]


def test_scales_the_0_1_utilization_and_epoch_second_reset_onto_the_probes_window_id() -> None:
    assert claudeRateLimitEventToUpdate(
        {
            "status": "allowed_warning",
            "rateLimitType": "seven_day",
            "utilization": 0.85,
            "resetsAt": 1_784_000_000,
        },
        noNames,
    ) == {
        "windows": [
            {
                "id": "seven_day",
                "kind": "weekly",
                "label": "Weekly",
                "usedPercent": 85,
                "windowDurationMins": 10080,
                "resetsAt": "2026-07-14T03:33:20.000Z",
            }
        ]
    }


def test_lands_the_streamed_overage_included_bucket_on_the_row_the_probe_named() -> None:
    event = {"status": "allowed", "rateLimitType": "seven_day_overage_included", "utilization": 0.4}
    assert claudeRateLimitEventToUpdate(event, noNames) is None
    assert claudeRateLimitEventToUpdate(event, {"overageIncluded": "Fable"}) == {
        "windows": [
            {
                "id": "seven_day_fable",
                "kind": "weekly",
                "label": "Weekly · Fable",
                "usedPercent": 40,
                "windowDurationMins": 10080,
            }
        ]
    }


def test_ignores_windows_the_page_does_not_render_and_events_without_a_utilization() -> None:
    assert (
        claudeRateLimitEventToUpdate(
            {"status": "allowed", "rateLimitType": "seven_day_opus", "utilization": 0.1}, noNames
        )
        is None
    )
    assert (
        claudeRateLimitEventToUpdate({"status": "rejected", "rateLimitType": "five_hour"}, noNames)
        is None
    )
