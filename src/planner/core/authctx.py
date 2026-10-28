"""Truthful request identity: who is asking, before anything asks what they may do.

The request boundary translates transport headers into one shared Principal. An
unattributed browser or ordinary CLI request is the owner. Employee requests name
the Chief, a Ticket, or a Manager. Header names stay private to this adapter.

Whether that principal may act on a given thing is planner.core.authority, which asks
one question. The guards that used to live here each named their own set of principal
kinds, and the sets were the two-doors shape written down.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from fastapi import Request

from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
    principal_legacy_actor,
)
from planner.core.errors import ErrorCode, PlannerError

X_PLAN_GOAL_ID: Final = "X-Plan-Goal-ID"
PLAN_GOAL_ID_SCOPE_KEY: Final = "planner.request_goal_id"
X_PLAN_ACTOR: Final = "X-Plan-Actor"
X_PLAN_TICKET_ID: Final = "X-Plan-Ticket-ID"
X_PLAN_MANAGER_ID: Final = "X-Plan-Manager-ID"
X_PLAN_SPRINT_ITEM_ID: Final = "X-Plan-Sprint-Item-ID"
PLAN_SPRINT_ITEM_ID_SCOPE_KEY: Final = "planner.request_sprint_item_id"
PLAN_ACTOR_SCOPE_KEY: Final = "planner.request_actor"
PLAN_TICKET_ID_SCOPE_KEY: Final = "planner.request_ticket_id"
PLAN_MANAGER_ID_SCOPE_KEY: Final = "planner.request_manager_id"


@dataclass(frozen=True)
class RequestContext:
    principal: Principal


def _normalize(raw: str | None) -> str | None:
    """Strip the header; whitespace-only values are absent."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def normalize_manager_identity(current: str | None, legacy: str | None) -> str | None:
    """Accept either issued identity generation, but never two different Managers."""
    current, legacy = _normalize(current), _normalize(legacy)
    if current is not None and legacy is not None and current != legacy:
        raise PlannerError(ErrorCode.agent_forbidden, "conflicting Manager identity claims", {})
    return current or legacy


def _classify(
    actor: str | None,
    ticket_id: str | None = None,
    manager_id: str | None = None,
    goal_id: str | None = None,
) -> RequestContext:
    normalized_ticket_id = _normalize(ticket_id)
    normalized_manager_id = _normalize(manager_id)
    if actor is None:
        return RequestContext(OWNER_PRINCIPAL)
    if actor == "goal" and _normalize(goal_id) is not None:
        return RequestContext(Principal(PrincipalKind.goal, str(_normalize(goal_id))))
    if actor == "chief":
        return RequestContext(CHIEF_PRINCIPAL)
    if actor == "worker" and normalized_ticket_id is not None:
        return RequestContext(Principal(PrincipalKind.ticket, normalized_ticket_id))
    if actor in {"manager", "sprint_item_supervisor"} and normalized_manager_id is not None:
        return RequestContext(Principal(PrincipalKind.manager, normalized_manager_id))
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "request actor does not name a Panels principal",
        {"actor": actor},
    )


def request_context(request: Request) -> RequestContext:
    """Normalize the actor and its optional truthful local Ticket claim."""
    actor = (
        request.scope[PLAN_ACTOR_SCOPE_KEY]
        if PLAN_ACTOR_SCOPE_KEY in request.scope
        else request.headers.get(X_PLAN_ACTOR)
    )
    ticket_id = (
        request.scope[PLAN_TICKET_ID_SCOPE_KEY]
        if PLAN_TICKET_ID_SCOPE_KEY in request.scope
        else request.headers.get(X_PLAN_TICKET_ID)
    )
    manager_id = (
        request.scope[PLAN_MANAGER_ID_SCOPE_KEY]
        if PLAN_MANAGER_ID_SCOPE_KEY in request.scope
        else request.headers.get(X_PLAN_MANAGER_ID)
    )
    legacy_manager_id = (
        request.scope[PLAN_SPRINT_ITEM_ID_SCOPE_KEY]
        if PLAN_SPRINT_ITEM_ID_SCOPE_KEY in request.scope
        else request.headers.get(X_PLAN_SPRINT_ITEM_ID)
    )
    manager_id = normalize_manager_identity(manager_id, legacy_manager_id)
    goal_id = request.scope.get(PLAN_GOAL_ID_SCOPE_KEY, request.headers.get(X_PLAN_GOAL_ID))
    return _classify(_normalize(actor), _normalize(ticket_id), manager_id, _normalize(goal_id))


def require_owner_or_chief(ctx: RequestContext) -> None:
    """Permit the owner or the Chief itself.

    The Chief's recap is the Chief's own report of its work, the way a Ticket's recap is
    its Worker's. Owner-only on that write makes the field unwritable by the one principal
    whose work it describes.
    """
    if ctx.principal.kind in (PrincipalKind.owner, PrincipalKind.chief):
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "this operation is available only to the owner or the Chief",
        {"actor": principal_legacy_actor(ctx.principal)},
    )


def require_owner(ctx: RequestContext) -> None:
    """Permit only Khushal's direct principal.

    Not authority over anything, which is why it is not the one rule. It marks the two
    places that record that *the owner* did something: the owner's own conversation read,
    and the owner's own send. Nobody stands above those by being above a Ticket.
    """
    if ctx.principal.kind is PrincipalKind.owner:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "this operation is available only to the owner",
        {"actor": principal_legacy_actor(ctx.principal)},
    )
