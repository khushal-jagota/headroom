"""Write admission: who may write what, when."""

from __future__ import annotations

from datetime import date
from typing import Final

from planner.core.contracts import (
    ErrorCode,
    PlannerError,
    Principal,
    PrincipalKind,
    principal_legacy_actor,
)
from planner.worker_types.contracts import WorkerTypeDefinition

REVISION_GUIDANCE_MAX_CHARACTERS: Final = 10_000


def require_direct_principal(principal: Principal, action: str) -> None:
    if principal.kind not in {PrincipalKind.owner, PrincipalKind.chief}:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} is a direct-only action",
            {"action": action, "actor": principal_legacy_actor(principal)},
        )


def require_ticket_principal(principal: Principal, action: str) -> None:
    """Require a Ticket identity for Worker-only writes."""
    if principal.kind is not PrincipalKind.ticket:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} is only available to a Worker",
            {"action": action, "actor": principal_legacy_actor(principal)},
        )


def require_direct_or_supervisor_principal(principal: Principal, action: str) -> None:
    if principal.kind not in {
        PrincipalKind.owner,
        PrincipalKind.chief,
        PrincipalKind.sprint_item,
    }:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} requires a direct user or Sprint Item supervisor",
            {"action": action, "actor": principal_legacy_actor(principal)},
        )


def check_agent_proposal(
    stage: str,
    field: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> None:
    """Whether this Ticket can take a worker submission on this field at all.

    The ceiling is not asked here. A ceiling names the last thing a worker is allowed to
    do, so reaching it decides whether the answer parks for approval, never whether the
    worker is allowed to answer.
    """
    if worker_type_definition.is_terminal(stage):
        raise PlannerError(
            ErrorCode.validation, "no proposals on a terminal ticket", {"stage": stage}
        )
    gating = worker_type_definition.gating_field(stage)
    if gating is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket stage has no proposal field",
            {"stage": stage},
        )
    if field != gating:
        raise PlannerError(
            ErrorCode.validation,
            "agents may propose only the current gating field",
            {"field": field, "gating_field": gating, "stage": stage},
        )


def validate_title(title: str, max_chars: int) -> None:
    if not title:
        raise PlannerError(ErrorCode.validation, "title must be non-empty")
    if len(title) > max_chars:
        raise PlannerError(
            ErrorCode.title_too_long,
            f"title exceeds {max_chars} characters",
            {"length": len(title), "max": max_chars},
        )


def validate_deadline(deadline: str | None) -> None:
    if deadline is None:
        return
    try:
        date.fromisoformat(deadline)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation,
            "deadline must be an ISO date",
            {"deadline": deadline},
        ) from exc


def validate_body(body: str, what: str) -> None:
    if not body:
        raise PlannerError(ErrorCode.validation, f"{what} must be non-empty")


def validate_revision_guidance(message: str) -> None:
    validate_body(message, "revision guidance")
    if len(message) > REVISION_GUIDANCE_MAX_CHARACTERS:
        raise PlannerError(
            ErrorCode.validation,
            f"revision guidance must be at most {REVISION_GUIDANCE_MAX_CHARACTERS} characters",
            {"maximum_characters": REVISION_GUIDANCE_MAX_CHARACTERS},
        )
