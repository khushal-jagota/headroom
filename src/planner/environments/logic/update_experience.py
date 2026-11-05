"""Update outcomes and display decisions from one coherent server snapshot."""
from __future__ import annotations

from datetime import datetime, timedelta

from planner.environments.deployment_lifecycle import DeploymentLifecycle
from planner.environments.update_configuration import UPDATE_ATTEMPT_TIMEOUT_SECONDS
from planner.environments.update_contracts import (
    OwnerUpdateRequest,
    PublishedUpdate,
    UpdateAction,
    UpdateExperienceState,
    UpdateRequestState,
)

ACTIVE_STATES = frozenset({"pending", "dispatched", "applying"})


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("update timestamp requires a time zone")
    return parsed


def request_deadline(request: OwnerUpdateRequest) -> datetime | None:
    if request.deadline_at is not None:
        return instant(request.deadline_at)
    if request.state in {"dispatched", "applying"}:
        # Older receipts did not record dispatch. Their creation time is the only
        # durable lower bound; never grant a fresh budget after each restart.
        return instant(request.dispatched_at or request.created_at) + timedelta(
            seconds=UPDATE_ATTEMPT_TIMEOUT_SECONDS
        )
    return None


def matching_lifecycle(
    request: OwnerUpdateRequest, lifecycle: DeploymentLifecycle | None,
) -> bool:
    return bool(
        lifecycle is not None
        and lifecycle.deployment_id == (request.deployment_id or request.request_id)
        and lifecycle.requested_sha == request.release.manifest.app_sha
    )


def settlement(
    request: OwnerUpdateRequest, lifecycle: DeploymentLifecycle | None,
    app_sha: str | None, now: datetime,
) -> tuple[UpdateRequestState, str] | None:
    if request.state not in ACTIVE_STATES:
        return None
    matches = matching_lifecycle(request, lifecycle)
    if matches and lifecycle is not None:
        target = request.release.manifest.app_sha
        if (app_sha == target and lifecycle.serving_sha == target
                and lifecycle.phase in {"app_healthy", "succeeded", "failed", "unknown"}):
            return "applied", "The requested version is installed."
        if lifecycle.phase in {"failed", "rolled_back", "cancelled"}:
            return "failed", lifecycle.detail or "The update did not install."
    if request.state == "pending" and instant(request.release.expires_at) <= now:
        return "failed", "The update expired before installation."
    deadline = request_deadline(request)
    if deadline is not None and deadline <= now:
        return "failed", "The update did not complete before its deadline. Try again."
    return None


def effective_request(
    request: OwnerUpdateRequest | None, lifecycle: DeploymentLifecycle | None,
) -> OwnerUpdateRequest | None:
    # A later ordinary deployment supersedes old owner receipts. An active owner
    # request still owns its own attempt and cannot inherit an unrelated lifecycle.
    if (request is not None and request.state not in ACTIVE_STATES
            and lifecycle is not None and not matching_lifecycle(request, lifecycle)
            and lifecycle.updated_at >= instant(request.created_at)):
        request = None
    return request


def experience(
    request: OwnerUpdateRequest | None, lifecycle: DeploymentLifecycle | None,
    available: PublishedUpdate | None, configured: bool, app_sha: str | None,
    now: datetime,
) -> tuple[UpdateExperienceState, tuple[UpdateAction, ...]]:
    usable = available is not None and instant(available.expires_at) > now and configured
    request = effective_request(request, lifecycle)
    relevant = lifecycle is not None and (
        request is None or matching_lifecycle(request, lifecycle)
    ) and (request is None or request.state != "cancelled")
    if request is not None and request.state == "failed":
        return "failed", ("retry",) if usable else ()
    if request is not None and request.state == "pending":
        if request.mode == "idle":
            return "waiting", ("cancel", "update_now")
        return "updating", ()
    if request is not None and request.state in {"dispatched", "applying"}:
        if relevant and lifecycle is not None and lifecycle.phase in {
            "restarting", "verifying", "app_healthy",
        }:
            return "restarting", ()
        return "updating", ()
    if relevant and lifecycle is not None:
        healthy = lifecycle.serving_sha == app_sha and app_sha == lifecycle.requested_sha
        if healthy and lifecycle.phase in {"app_healthy", "succeeded", "failed", "unknown"}:
            # The browser owns completion visibility: only a different boot SHA
            # means that this page still needs Refresh.
            return ("available", ("update",)) if usable else ("up_to_date", ())
        if lifecycle.phase in {"failed", "rolled_back", "unknown"}:
            return "failed", ("retry",) if usable else ()
        if lifecycle.phase in {"preparing", "restarting", "verifying"}:
            if lifecycle.expires_at <= now:
                return "failed", ("retry",) if usable else ()
            return ("updating" if lifecycle.phase == "preparing" else "restarting"), ()
    return ("available", ("update",)) if usable else ("up_to_date", ())
