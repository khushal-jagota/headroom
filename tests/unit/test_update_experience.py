"""The update card's state comes only from the attempt record, the ready release and time."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from tests.support.update_apps import SHA_A, SHA_B, release

from planner.environments.logic.update_experience import (
    MAC_FAILED_DETAIL,
    MAC_UNREACHABLE_DETAIL,
    UNFINISHED_DETAIL,
    deployment_status,
    resolve,
    settle,
)
from planner.environments.update_contracts import PublishedMacUpdate, UpdateAttempt

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
READY = release()
NEWER = replace(READY, manifest=replace(READY.manifest, app_sha="c" * 40))


def attempt(state: str, **changes: object) -> UpdateAttempt:
    value = UpdateAttempt(
        attempt_id="a1", from_sha=SHA_A, to_sha=SHA_B, mode="now", confirm_activity=True,
        state=state,  # type: ignore[arg-type]
        created_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        release=replace(READY, mac=PublishedMacUpdate(9, "f" * 64, 10)),
        mac_attempt_id="m1",
    )
    return replace(value, **changes)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("record", "available", "running", "mac_applies", "mac_phase", "seconds", "expected"),
    [
        (None, None, SHA_A, True, None, 0, ("up_to_date", ())),
        (None, READY, SHA_A, True, None, 0, ("available", ("update",))),
        (attempt("waiting", mode="idle"), READY, SHA_A, True, None, 0,
         ("waiting", ("cancel", "update_now"))),
        (attempt("waiting", deadline_at="2026-11-05T12:15:00+00:00"), READY, SHA_A, True,
         None, 0, ("updating", ())),
        (attempt("preparing", deadline_at="2026-11-05T12:15:00+00:00"), READY, SHA_A, True,
         None, 0, ("updating", ())),
        (attempt("installing", deadline_at=(NOW + timedelta(minutes=15)).isoformat()), READY,
         SHA_A, True, None, 0, ("updating", ())),
        (attempt("failed", detail="no"), READY, SHA_A, True, None, 0, ("failed", ("retry",))),
        (attempt("failed", detail="no"), None, SHA_A, True, None, 0, ("failed", ())),
        # A newer ready release replaces the old failure with a fresh offer.
        (attempt("failed", detail="no"), NEWER, SHA_A, True, None, 0, ("available", ("update",))),
        (attempt("done"), None, SHA_B, False, None, 0, ("up_to_date", ())),
        (attempt("done"), NEWER, SHA_B, True, "current", 0, ("available", ("update",))),
        (attempt("done"), None, SHA_B, True, "ready_to_restart", 89, ("updating", ())),
        (attempt("done"), None, SHA_B, True, None, 91, ("mac_unreachable", ("retry_mac",))),
        (attempt("done"), None, SHA_B, True, "downloading", 91,
         ("mac_unreachable", ("retry_mac",))),
        # A Mac that keeps reporting while it waits or downloads is still on its way.
        (attempt("done"), None, SHA_B, True, "ready_to_restart", -1, ("updating", ())),
        (attempt("done"), None, SHA_B, True, "failed", 0, ("mac_failed", ("retry_mac",))),
        (attempt("done"), None, SHA_B, True, "reverted", 500, ("mac_failed", ("retry_mac",))),
        # A Mac that reports current later moves the card on by itself.
        (attempt("done"), None, SHA_B, True, "current", 500, ("up_to_date", ())),
        # A done record this server does not run is history, not a Mac wait.
        (attempt("done"), None, SHA_A, True, None, 500, ("up_to_date", ())),
    ],
)
def test_every_card_state(
    record: UpdateAttempt | None,
    available: object,
    running: str,
    mac_applies: bool,
    mac_phase: str | None,
    seconds: float,
    expected: tuple[str, tuple[str, ...]],
) -> None:
    """Each card state and its actions follow from exactly the inputs the contract names.

    A negative `seconds` means the Mac reported 100 s after the VPS finished, and the
    card is read 150 s after the VPS finished.
    """
    reported = NOW + timedelta(seconds=100) if seconds < 0 else None
    now = NOW + timedelta(seconds=150 if seconds < 0 else seconds)
    state, actions, detail, deadline = resolve(
        record, available, running,  # type: ignore[arg-type]
        mac_applies=mac_applies, mac_phase=mac_phase, mac_reported_at=reported,
        mac_report_seconds=90, now=now,
    )
    assert (state, actions) == expected
    assert detail == {
        "failed": record.detail if record else None,
        "mac_failed": MAC_FAILED_DETAIL,
        "mac_unreachable": MAC_UNREACHABLE_DETAIL,
    }.get(state)
    if state != "updating":
        assert deadline is None
    elif record is not None and record.state == "done":
        # Until when the Mac may stay silent: its last report, or the VPS finish, plus 90 s.
        assert deadline == ((reported or NOW) + timedelta(seconds=90)).isoformat()
    else:
        assert deadline == record.deadline_at  # type: ignore[union-attr]


def test_installing_settles_from_the_running_sha_only_after_its_deadline() -> None:
    """Every attempt ends: a lost installer cannot leave the card updating forever."""
    record = attempt("installing", deadline_at=(NOW + timedelta(seconds=900)).isoformat())
    assert settle(record, SHA_B, NOW + timedelta(seconds=900)) is None
    late = NOW + timedelta(seconds=901)
    done = settle(record, SHA_B, late)
    failed = settle(record, SHA_A, late)
    assert done is not None and (done.state, done.detail) == ("done", None)
    assert failed is not None and (failed.state, failed.detail) == ("failed", UNFINISHED_DETAIL)
    for other in ("waiting", "preparing", "done", "failed"):
        assert settle(replace(record, state=other), SHA_B, late) is None


def test_deployment_status_keeps_the_ios_shape() -> None:
    """iOS builds in the field decode `/api/deployment-status`; it moved to the attempt."""
    def body(record: UpdateAttempt | None, seconds: float = 0) -> dict[str, object]:
        return deployment_status(
            record, SHA_B, success_seconds=300, now=NOW + timedelta(seconds=seconds)
        )

    assert body(None)["state"] == "idle"
    assert body(attempt("installing", deadline_at="x"))["state"] == "restarting"
    assert body(attempt("failed", detail="no"))["outcome"] == "failed"
    assert body(attempt("failed", detail="no"), 301)["state"] == "idle"
    assert body(attempt("done"))["state"] == "back_up"
    assert body(attempt("done"), 301)["state"] == "idle"
    assert set(body(None)) == {
        "state", "deployed_sha", "target_sha", "outcome", "detail", "valid_until",
        "success_display_until",
    }
