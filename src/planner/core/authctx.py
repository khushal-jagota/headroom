"""Truthful request identity: who is asking, before anything asks what they may do.

The request boundary translates transport headers into one shared Principal. A request
that carries the owner credential cookie is the owner, whatever else it says. Employee
requests name the Chief, a Ticket, or a Manager. A request that does neither is
unidentified: it stands above nothing. Header names stay private to this adapter.

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
    UNIDENTIFIED_PRINCIPAL,
    Principal,
    PrincipalKind,
    principal_legacy_actor,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.core.owner_credential import OWNER_CREDENTIAL_COOKIE, presents_owner_credential

X_PLAN_CONVERSATION_ID: Final = "X-Plan-Conversation-ID"
X_PLAN_ACTOR: Final = "X-Plan-Actor"
X_PLAN_TICKET_ID: Final = "X-Plan-Ticket-ID"
X_PLAN_MANAGER_ID: Final = "X-Plan-Manager-ID"


@dataclass(frozen=True)
class RequestContext:
    principal: Principal
    conversation_id: str | None = None


def _normalize(raw: str | None) -> str | None:
    """Strip the header; whitespace-only values are absent."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _classify(
    actor: str | None,
    ticket_id: str | None = None,
    manager_id: str | None = None,
) -> RequestContext:
    normalized_ticket_id = _normalize(ticket_id)
    normalized_manager_id = _normalize(manager_id)
    if actor is None:
        return RequestContext(UNIDENTIFIED_PRINCIPAL)
    if actor == "chief":
        return RequestContext(CHIEF_PRINCIPAL)
    if actor == "worker" and normalized_ticket_id is not None:
        return RequestContext(Principal(PrincipalKind.ticket, normalized_ticket_id))
    if actor == "manager" and normalized_manager_id is not None:
        return RequestContext(Principal(PrincipalKind.manager, normalized_manager_id))
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "request actor does not name a Panels principal",
        {"actor": actor},
    )


def request_context(request: Request) -> RequestContext:
    """The owner by cookie, else the actor and its optional truthful local Ticket claim."""
    app = request.scope.get("app")
    if presents_owner_credential(
        request.cookies.get(OWNER_CREDENTIAL_COOKIE),
        getattr(getattr(app, "state", None), "owner_credential", None),
    ):
        return RequestContext(OWNER_PRINCIPAL)
    classified = _classify(
        _normalize(request.headers.get(X_PLAN_ACTOR)),
        _normalize(request.headers.get(X_PLAN_TICKET_ID)),
        request.headers.get(X_PLAN_MANAGER_ID),
    )
    return RequestContext(
        classified.principal, _normalize(request.headers.get(X_PLAN_CONVERSATION_ID))
    )


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
