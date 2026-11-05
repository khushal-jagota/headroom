"""Update outcomes and display decisions from one coherent server snapshot."""
from __future__ import annotations

from datetime import datetime

from planner.environments.deployment_lifecycle import DeploymentLifecycle
from planner.environments.update_contracts import (
    OwnerUpdateRequest,
    PublishedUpdate,
    UpdateAction,
    UpdateExperienceState,
)

CANCELLED_DETAIL = "Update canceled before installation. The previous version stayed active."

ACTIVE_STATES = frozenset({"requested", "installing"})


def instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("update timestamp requires a time zone")
    return parsed


def matching_lifecycle(
    request: OwnerUpdateRequest, lifecycle: DeploymentLifecycle | None,
) -> bool:
    return bool(
        lifecycle is not None
        and lifecycle.deployment_id == request.request_id
        and lifecycle.requested_sha == request.release.manifest.app_sha
    )


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
    ) and (request is None or request.detail != CANCELLED_DETAIL)
    if (request is not None and request.state == "failed"
            and request.detail != CANCELLED_DETAIL):
        return "failed", ("retry",) if usable else ()
    if request is not None and request.state == "requested":
        if request.mode == "idle":
            return "waiting", ("cancel", "update_now")
        return "updating", ()
    if request is not None and request.state == "installing":
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
            return ("updating" if lifecycle.phase == "preparing" else "restarting"), ()
    return ("available", ("update",)) if usable else ("up_to_date", ())
