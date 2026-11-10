"""What the owner sees of an update, from one attempt record and one moment."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

from planner.environments.update_contracts import (
    PublishedUpdate,
    UpdateAction,
    UpdateAttempt,
    UpdateExperienceState,
)

UNFINISHED_DETAIL = (
    "The update did not finish in time, so Panels stayed on the previous version."
)
MAC_FAILED_DETAIL = "Panels updated, but the Mac did not. It went back to its previous version."
MAC_UNREACHABLE_DETAIL = "The Mac did not check in during the update. It can be asleep or offline."

def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("update timestamp requires a time zone")
    return parsed


def settle(attempt: UpdateAttempt, app_sha: str | None, now: datetime) -> UpdateAttempt | None:
    """The end of an installing attempt whose deadline passed; None while it may still run.

    The running SHA is the evidence: Panels either answers as the new version or not.
    """
    if (
        attempt.state != "installing"
        or attempt.deadline_at is None
        or now <= instant(attempt.deadline_at)
    ):
        return None
    if attempt.to_sha == app_sha:
        return replace(attempt, state="done", detail=None)
    return replace(attempt, state="failed", detail=UNFINISHED_DETAIL)


def resolve(
    attempt: UpdateAttempt | None,
    available: PublishedUpdate | None,
    app_sha: str | None,
    *,
    mac_applies: bool,
    mac_phase: str | None,
    mac_reported_at: datetime | None,
    mac_report_seconds: float,
    now: datetime,
) -> tuple[UpdateExperienceState, tuple[UpdateAction, ...], str | None, str | None]:
    """State, allowed actions, failure detail and connection deadline for the update card.

    `mac_applies` is false when the release has no Mac part or no executor is configured.
    `mac_phase` and `mac_reported_at` are the Mac's last report for `mac_attempt_id`.
    Every `updating` answer carries the time until which it still counts as updating.
    """
    idle: tuple[UpdateExperienceState, tuple[UpdateAction, ...], str | None, str | None] = (
        ("available", ("update",), None, None) if available is not None
        else ("up_to_date", (), None, None)
    )
    if attempt is None:
        return idle
    if attempt.state == "waiting":
        if attempt.mode == "idle":
            return "waiting", ("cancel", "update_now"), None, None
        return "updating", (), None, attempt.deadline_at
    if attempt.state in {"preparing", "installing"}:
        return "updating", (), None, attempt.deadline_at
    if attempt.state == "failed":
        if available is None:
            return "failed", (), attempt.detail, None
        if available.manifest.app_sha != attempt.to_sha:
            return idle
        return "failed", ("retry",), attempt.detail, None
    # Done. The VPS part is finished; only the Mac can still be on its way.
    if attempt.to_sha != app_sha or not mac_applies or mac_phase == "current":
        return idle
    if mac_phase in {"failed", "reverted"}:
        return "mac_failed", ("retry_mac",), MAC_FAILED_DETAIL, None
    # A Mac that keeps reporting is on its way; silence past the bound is unreachable.
    heard = instant(attempt.updated_at)
    if mac_reported_at is not None and mac_reported_at > heard:
        heard = mac_reported_at
    silent_after = heard + timedelta(seconds=mac_report_seconds)
    if now > silent_after:
        return "mac_unreachable", ("retry_mac",), MAC_UNREACHABLE_DETAIL, None
    return "updating", (), None, silent_after.isoformat()


def deployment_status(
    attempt: UpdateAttempt | None, app_sha: str | None, *, success_seconds: float, now: datetime
) -> dict[str, object]:
    """The small `/api/deployment-status` body that iOS builds in the field still read."""
    body: dict[str, object] = {
        "state": "idle", "deployed_sha": app_sha, "target_sha": None, "outcome": None,
        "detail": None, "valid_until": None, "success_display_until": None,
    }
    if attempt is None or attempt.state == "waiting":
        return body
    body["target_sha"] = attempt.to_sha
    if attempt.state == "preparing":
        body["state"] = "preparing"
    elif attempt.state == "installing":
        body.update(state="restarting", valid_until=attempt.deadline_at)
    elif attempt.state == "failed":
        # The update card keeps the failure and its Try again; iOS shows it for a while.
        if now < instant(attempt.updated_at) + timedelta(seconds=success_seconds):
            body.update(state="problem", outcome="failed", detail=attempt.detail)
    elif attempt.to_sha == app_sha:
        until = instant(attempt.updated_at) + timedelta(seconds=success_seconds)
        body["outcome"] = "succeeded"
        if now < until:
            body.update(state="back_up", valid_until=until.isoformat(),
                        success_display_until=until.isoformat())
    return body
