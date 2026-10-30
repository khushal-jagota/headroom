"""All five upstream providerUsageLimits.test.ts cases, unchanged assertions."""

from typing import Any

from planner.conversation.usage.providerUsageLimits import (
    applyUsageLimitsUpdate,
    resolveUsageLimitsAfterProbe,
)

checkedAt = "2026-09-03T12:00:00.000Z"
session = {
    "id": "five_hour",
    "kind": "session",
    "label": "Session",
    "usedPercent": 40,
    "windowDurationMins": 300,
    "resetsAt": "2026-09-03T14:00:00.000Z",
}
weekly = {
    "id": "seven_day",
    "kind": "weekly",
    "label": "Weekly",
    "usedPercent": 20,
    "windowDurationMins": 10_080,
}
published: dict[str, Any] = {"checkedAt": checkedAt, "windows": [session, weekly]}


def test_returns_the_published_object_itself_when_no_window_moved() -> None:
    next = applyUsageLimitsUpdate(
        {
            "previous": published,
            "checkedAt": "2026-09-03T12:00:05.000Z",
            "update": {
                "windows": [
                    {**weekly},
                    {"id": "five_hour", "kind": "session", "label": "Session", "usedPercent": 40},
                ]
            },
        }
    )
    assert next is published


def test_upserts_by_id_and_keeps_the_reset_a_percent_only_update_omits() -> None:
    next = applyUsageLimitsUpdate(
        {
            "previous": published,
            "checkedAt": "2026-09-03T12:00:05.000Z",
            "update": {
                "windows": [
                    {"id": "five_hour", "kind": "session", "label": "Session", "usedPercent": 55}
                ]
            },
        }
    )
    assert next is not published
    assert next == {
        "checkedAt": "2026-09-03T12:00:05.000Z",
        "windows": [{**session, "usedPercent": 55}, weekly],
    }


def test_leaves_an_unsupported_account_and_an_empty_update_alone() -> None:
    unsupported = {"checkedAt": checkedAt, "windows": [], "unavailable": {"reason": "unsupported"}}
    assert (
        applyUsageLimitsUpdate(
            {"previous": unsupported, "checkedAt": checkedAt, "update": {"windows": [session]}}
        )
        is unsupported
    )
    assert (
        applyUsageLimitsUpdate(
            {"previous": published, "checkedAt": checkedAt, "update": {"windows": []}}
        )
        is published
    )


def test_preserves_reset_credits_when_a_streamed_window_update_changes_usage() -> None:
    resetCredits = {"availableCount": 2, "nextExpiresAt": "2026-10-01T00:00:00.000Z"}
    next = applyUsageLimitsUpdate(
        {
            "previous": {**published, "resetCredits": resetCredits},
            "checkedAt": "2026-09-03T12:00:05.000Z",
            "update": {"windows": [{**session, "usedPercent": 55}]},
        }
    )
    assert next == {
        "checkedAt": "2026-09-03T12:00:05.000Z",
        "windows": [{**session, "usedPercent": 55}, weekly],
        "resetCredits": resetCredits,
    }


def test_keeps_the_last_good_windows_through_a_failed_probe_but_not_an_unsupported_one() -> None:
    failed = {"checkedAt": checkedAt, "windows": [], "unavailable": {"reason": "probeFailed"}}
    unsupported = {"checkedAt": checkedAt, "windows": [], "unavailable": {"reason": "unsupported"}}
    assert resolveUsageLimitsAfterProbe({"published": published, "probed": failed}) is published
    assert (
        resolveUsageLimitsAfterProbe({"published": published, "probed": unsupported}) is unsupported
    )
    assert resolveUsageLimitsAfterProbe({"published": None, "probed": failed}) is failed
