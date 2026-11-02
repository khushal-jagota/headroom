"""All upstream codexUsageLimits.test.ts cases, with their original inputs."""

from typing import Any

import pytest

from planner.conversation.usage.codexUsageLimits import (
    codexRateLimitsFailureMessage,
    codexRateLimitsToLimits,
    codexRateLimitsToUpdate,
    codexResetCreditsToContract,
    codexUsageLimitMessage,
    mergeCodexRateLimits,
)

checkedAt = "2026-07-18T10:00:00.000Z"


def test_maps_primary_and_secondary_onto_the_session_and_weekly_windows() -> None:
    assert codexRateLimitsToLimits(
        {
            "checkedAt": checkedAt,
            "snapshot": {
                "planType": "plus",
                "primary": {
                    "usedPercent": 12,
                    "resetsAt": 1_784_000_000,
                    "windowDurationMins": 300,
                },
                "secondary": {
                    "usedPercent": 47,
                    "resetsAt": 1_784_500_000,
                    "windowDurationMins": 10080,
                },
            },
        }
    ) == {
        "checkedAt": checkedAt,
        "windows": [
            {
                "id": "primary",
                "kind": "session",
                "label": "Session",
                "usedPercent": 12,
                "windowDurationMins": 300,
                "resetsAt": "2026-07-14T03:33:20.000Z",
            },
            {
                "id": "secondary",
                "kind": "weekly",
                "label": "Weekly",
                "usedPercent": 47,
                "windowDurationMins": 10080,
                "resetsAt": "2026-07-19T22:26:40.000Z",
            },
        ],
    }


def test_treats_a_lone_duration_less_primary_as_monthly_on_free_and_go() -> None:
    assert codexRateLimitsToLimits(
        {
            "checkedAt": checkedAt,
            "snapshot": {"planType": "free", "primary": {"usedPercent": 80, "resetsAt": None}},
        }
    )["windows"] == [
        {
            "id": "primary",
            "kind": "monthly",
            "label": "Monthly",
            "usedPercent": 80,
            "windowDurationMins": 43_200,
        }
    ]


def test_selects_the_main_codex_allowance_and_leaves_spark_out() -> None:
    spark = {
        "limitId": "codex_bengalfox",
        "primary": {"usedPercent": 0, "windowDurationMins": 300},
        "secondary": {"usedPercent": 90, "windowDurationMins": 10080},
    }
    assert codexRateLimitsToLimits(
        {
            "checkedAt": checkedAt,
            "snapshot": spark,
            "rateLimitsByLimitId": {
                "codex_bengalfox": spark,
                "codex": {"secondary": {"usedPercent": 42, "windowDurationMins": 10080}},
            },
        }
    )["windows"] == [
        {
            "id": "secondary",
            "kind": "weekly",
            "label": "Weekly",
            "usedPercent": 42,
            "windowDurationMins": 10080,
        }
    ]


@pytest.mark.parametrize(
    "rateLimitsByLimitId", [None, None, {}], ids=["undefined", "null", "empty"]
)
def test_supports_legacy_reads_with_no_bucket_map(rateLimitsByLimitId: Any) -> None:
    snapshot = {"primary": {"usedPercent": 12, "windowDurationMins": 300}}
    assert codexRateLimitsToLimits(
        {"checkedAt": checkedAt, "snapshot": snapshot, "rateLimitsByLimitId": rateLimitsByLimitId}
    ) == codexRateLimitsToLimits({"checkedAt": checkedAt, "snapshot": snapshot})


def test_does_not_show_a_model_specific_legacy_snapshot_as_the_main_allowance() -> None:
    assert (
        codexRateLimitsToLimits(
            {
                "checkedAt": checkedAt,
                "snapshot": {"limitId": "codex_bengalfox", "secondary": {"usedPercent": 90}},
            }
        )["windows"]
        == []
    )


def test_carries_only_the_windows_the_notification_names() -> None:
    assert codexRateLimitsToUpdate(
        {"secondary": {"usedPercent": 51, "windowDurationMins": 10080}}
    ) == {
        "windows": [
            {
                "id": "secondary",
                "kind": "weekly",
                "label": "Weekly",
                "usedPercent": 51,
                "windowDurationMins": 10080,
            }
        ]
    }
    assert codexRateLimitsToUpdate({"planType": "plus"}) is None


def test_ignores_spark_notifications_so_they_cannot_overwrite_the_main_allowance() -> None:
    assert (
        codexRateLimitsToUpdate(
            {
                "limitId": "codex_bengalfox",
                "primary": {"usedPercent": 0, "windowDurationMins": 300},
                "secondary": {"usedPercent": 90, "windowDurationMins": 10080},
            }
        )
        is None
    )
    result = codexRateLimitsToUpdate(
        {"limitId": "codex", "secondary": {"usedPercent": 42, "windowDurationMins": 10080}}
    )
    assert result is not None
    assert result["windows"] == [
        {
            "id": "secondary",
            "kind": "weekly",
            "label": "Weekly",
            "usedPercent": 42,
            "windowDurationMins": 10080,
        }
    ]


def test_keeps_the_json_rpc_code_and_nothing_else_from_a_request_failure() -> None:
    assert (
        codexRateLimitsFailureMessage(
            {
                "_tag": "CodexAppServerRequestError",
                "code": -32603,
                "errorMessage": (
                    "failed to fetch codex rate limits: GET "
                    "https://chatgpt.com/backend-api/wham/usage failed: 401 Unauthorized"
                ),
            }
        )
        == "Codex could not read usage (JSON-RPC -32603)."
    )


def test_phrases_a_dead_process_differently_from_a_bad_answer() -> None:
    assert (
        codexRateLimitsFailureMessage({"_tag": "CodexAppServerProcessExitedError", "code": 1})
        == "Codex exited before it could report usage."
    )


def test_counts_available_credits_and_reports_the_soonest_expiry() -> None:
    assert codexResetCreditsToContract(
        {
            "availableCount": 2,
            "credits": [
                {"status": "available", "expiresAt": 1_784_500_000},
                {"status": "redeemed", "expiresAt": 1_700_000_000},
                {"status": "available", "expiresAt": 1_784_000_000},
            ],
        }
    ) == {"availableCount": 2, "nextExpiresAt": "2026-07-14T03:33:20.000Z"}
    assert codexResetCreditsToContract({"availableCount": 0}) == {"availableCount": 0}
    assert codexResetCreditsToContract(None) is None


def test_rides_along_on_the_probes_limits() -> None:
    assert codexRateLimitsToLimits(
        {
            "checkedAt": checkedAt,
            "snapshot": {"primary": {"usedPercent": 5, "windowDurationMins": 300}},
            "resetCredits": {"availableCount": 1},
        }
    )["resetCredits"] == {"availableCount": 1}


at = "2026-01-01T00:00:00.000Z"
atSeconds = 1767225600


def test_names_the_exhausted_window_and_the_workspaces_missing_credits() -> None:
    assert codexUsageLimitMessage(
        {
            "limitId": "codex",
            "rateLimitReachedType": "workspace_owner_credits_depleted",
            "primary": {"usedPercent": 40, "resetsAt": atSeconds + 3600, "windowDurationMins": 300},
            "secondary": {
                "usedPercent": 100,
                "resetsAt": atSeconds + 5 * 86400 + 5 * 3600,
                "windowDurationMins": 10080,
            },
        },
        at,
    ) == (
        "Codex usage limit reached. The weekly limit resets in 5d 5h. "
        "The workspace has no credits to continue sooner: ask your workspace owner to add credits, "
        "or send the message again once the limit resets."
    )


def test_points_a_reached_spend_cap_at_the_workspace_owner() -> None:
    assert codexUsageLimitMessage(
        {
            "limitId": "codex",
            "rateLimitReachedType": "workspace_member_usage_limit_reached",
            "primary": {
                "usedPercent": 100,
                "resetsAt": atSeconds + 3 * 3600 + 20 * 60,
                "windowDurationMins": 300,
            },
        },
        at,
    ) == (
        "Codex usage limit reached. The session limit resets in 3h 20m. "
        "The workspace spend limit is reached: ask your workspace owner to raise it, "
        "or send the message again once the limit resets."
    )


def test_names_no_window_when_credits_run_out_without_one() -> None:
    assert codexUsageLimitMessage(
        {"limitId": "codex", "rateLimitReachedType": "workspace_member_credits_depleted"}, at
    ) == (
        "Codex usage limit reached. The workspace has no credits to continue sooner: "
        "ask your workspace owner to add credits, or send the message again once the limit resets."
    )


def test_says_only_what_it_knows_without_a_snapshot() -> None:
    assert (
        codexUsageLimitMessage(None, at)
        == "Codex usage limit reached. Send the message again once the limit resets."
    )


def test_keeps_windows_an_update_does_not_carry() -> None:
    assert mergeCodexRateLimits(
        {
            "limitId": "codex",
            "planType": "business",
            "primary": {"usedPercent": 100, "resetsAt": 1_800_000_000, "windowDurationMins": 300},
        },
        {"rateLimitReachedType": "rate_limit_reached"},
    ) == {
        "limitId": "codex",
        "planType": "business",
        "rateLimitReachedType": "rate_limit_reached",
        "primary": {"usedPercent": 100, "resetsAt": 1_800_000_000, "windowDurationMins": 300},
    }


def test_ignores_a_model_specific_snapshot_so_it_cannot_replace_the_main_allowance() -> None:
    main = {
        "limitId": "codex",
        "primary": {"usedPercent": 100, "resetsAt": 1_800_000_000, "windowDurationMins": 300},
    }
    assert (
        mergeCodexRateLimits(
            main,
            {
                "limitId": "spark",
                "primary": {"usedPercent": 3, "resetsAt": 1_800_000_000, "windowDurationMins": 300},
            },
        )
        is main
    )
