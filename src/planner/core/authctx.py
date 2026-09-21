"""Truthful request identity: who is asking, before anything asks what they may do.

The request boundary translates transport headers into one shared Principal. An
unattributed browser or ordinary CLI request is the owner. Employee requests name
the Chief, a Ticket, or a Sprint Item. Header names stay private to this adapter.

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

X_PLAN_ACTOR: Final = "X-Plan-Actor"
X_PLAN_TICKET_ID: Final = "X-Plan-Ticket-ID"
X_PLAN_SPRINT_ITEM_ID: Final = "X-Plan-Sprint-Item-ID"
PLAN_ACTOR_SCOPE_KEY: Final = "planner.request_actor"
PLAN_TICKET_ID_SCOPE_KEY: Final = "planner.request_ticket_id"
PLAN_SPRINT_ITEM_ID_SCOPE_KEY: Final = "planner.request_sprint_item_id"


@dataclass(frozen=True)
class RequestContext:
    principal: Principal


def _normalize(raw: str | None) -> str | None:
    """Strip the header; whitespace-only values are absent."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _classify(
    actor: str | None,
    ticket_id: str | None = None,
    sprint_item_id: str | None = None,
) -> RequestContext:
    normalized_ticket_id = _normalize(ticket_id)
    normalized_sprint_item_id = _normalize(sprint_item_id)
    if actor is None:
        return RequestContext(OWNER_PRINCIPAL)
    if actor == "chief":
        return RequestContext(CHIEF_PRINCIPAL)
    if actor == "worker" and normalized_ticket_id is not None:
        return RequestContext(Principal(PrincipalKind.ticket, normalized_ticket_id))
    if actor == "sprint_item_supervisor" and normalized_sprint_item_id is not None:
        return RequestContext(Principal(PrincipalKind.sprint_item, normalized_sprint_item_id))
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
    sprint_item_id = (
        request.scope[PLAN_SPRINT_ITEM_ID_SCOPE_KEY]
        if PLAN_SPRINT_ITEM_ID_SCOPE_KEY in request.scope
        else request.headers.get(X_PLAN_SPRINT_ITEM_ID)
    )
    return _classify(_normalize(actor), _normalize(ticket_id), _normalize(sprint_item_id))


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
