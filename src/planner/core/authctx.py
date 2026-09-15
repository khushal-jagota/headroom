"""Truthful request identity and the direct-write authority boundary.

The request boundary translates transport headers into one shared Principal. An
unattributed browser or ordinary CLI request is the owner. Employee requests name
the Chief, a Ticket, or a Sprint Item. Header names stay private to this adapter.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Final, Literal

from fastapi import Request

from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError

X_PLAN_ACTOR: Final = "X-Plan-Actor"
X_PLAN_TICKET_ID: Final = "X-Plan-Ticket-ID"
X_PLAN_SPRINT_ITEM_ID: Final = "X-Plan-Sprint-Item-ID"
PLAN_ACTOR_SCOPE_KEY: Final = "planner.request_actor"
PLAN_TICKET_ID_SCOPE_KEY: Final = "planner.request_ticket_id"
PLAN_SPRINT_ITEM_ID_SCOPE_KEY: Final = "planner.request_sprint_item_id"
_CHIEF_ACTOR: Final = "chief"
_WORKER_ACTOR: Final = "worker"
SPRINT_ITEM_SUPERVISOR_ACTOR: Final = "sprint_item_supervisor"

PlanningCapability = Literal[
    "planning-day",
    "planning-midday-check",
    "planning-sprint",
]
PLANNING_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {"planning-day", "planning-midday-check", "planning-sprint"}
)


@dataclass(frozen=True)
class RequestContext:
    principal: Principal

    @property
    def actor(self) -> str:
        """Return the existing audit-row value at the legacy data boundary."""
        return {
            PrincipalKind.owner: "unattributed",
            PrincipalKind.chief: _CHIEF_ACTOR,
            PrincipalKind.ticket: _WORKER_ACTOR,
            PrincipalKind.sprint_item: SPRINT_ITEM_SUPERVISOR_ACTOR,
        }[self.principal.kind]


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
    if actor == _CHIEF_ACTOR:
        return RequestContext(CHIEF_PRINCIPAL)
    if actor == _WORKER_ACTOR and normalized_ticket_id is not None:
        return RequestContext(Principal(PrincipalKind.ticket, normalized_ticket_id))
    if actor == SPRINT_ITEM_SUPERVISOR_ACTOR and normalized_sprint_item_id is not None:
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


def require_sprint_item_supervisor_read(
    conn: sqlite3.Connection, ctx: RequestContext, sprint_item_id: str
) -> None:
    """Permit direct readers or the exact durable supervisor for this normal item."""
    row = conn.execute(
        "SELECT 1 FROM sprint_items WHERE id=? AND kind='normal'", (sprint_item_id,)
    ).fetchone()
    if row is None:
        if ctx.principal.kind is PrincipalKind.sprint_item:
            _reject_sprint_item_supervisor_read(ctx, sprint_item_id)
        raise PlannerError(ErrorCode.not_found, "sprint item not found", {"id": sprint_item_id})
    if ctx.principal.kind in {PrincipalKind.owner, PrincipalKind.chief}:
        return
    if ctx.principal != Principal(PrincipalKind.sprint_item, sprint_item_id):
        _reject_sprint_item_supervisor_read(ctx, sprint_item_id)


def require_sprint_item_supervisor_ticket_write(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
) -> None:
    """Permit only the exact durable supervisor and its direct child Ticket."""
    if ctx.principal != Principal(PrincipalKind.sprint_item, sprint_item_id):
        _reject_sprint_item_supervisor_write(ctx, sprint_item_id, ticket_id)
    if not _is_current_child_ticket(conn, sprint_item_id, ticket_id):
        _reject_sprint_item_supervisor_write(ctx, sprint_item_id, ticket_id)


def _is_current_child_ticket(conn: sqlite3.Connection, sprint_item_id: str, ticket_id: str) -> bool:
    """Whether this Ticket is right now a child of that normal Sprint Item."""
    row = conn.execute(
        "SELECT 1 FROM sprint_items AS item "
        "JOIN tickets AS ticket ON ticket.sprint_item_id = item.id "
        "WHERE item.id = ? AND item.kind = 'normal' AND ticket.id = ?",
        (sprint_item_id, ticket_id),
    ).fetchone()
    return row is not None


def require_ticket_delete(
    conn: sqlite3.Connection, ctx: RequestContext, ticket_id: str
) -> str | None:
    """Admit one permanent Ticket deletion, and name the supervisor Item behind it.

    A direct caller deletes any Ticket and has no Item, so this returns ``None`` for one.
    A Sprint Item supervisor deletes only a current child of the Item its own request
    identity names, and that Item id comes back for the writer to recheck under its
    transaction. Every other actor is refused.
    """
    if ctx.principal.kind in {PrincipalKind.owner, PrincipalKind.chief}:
        return None
    if ctx.principal.kind is not PrincipalKind.sprint_item or not _is_current_child_ticket(
        conn, ctx.principal.id, ticket_id
    ):
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "Ticket deletion is not available to this actor",
            {
                "actor": ctx.actor,
                "sprint_item_id": (
                    ctx.principal.id if ctx.principal.kind is PrincipalKind.sprint_item else None
                ),
                "ticket_id": ticket_id,
            },
        )
    return ctx.principal.id


def _reject_sprint_item_supervisor_write(
    ctx: RequestContext, sprint_item_id: str, ticket_id: str
) -> None:
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "Ticket review is not available to this Sprint Item supervisor",
        {
            "actor": ctx.actor,
            "sprint_item_id": sprint_item_id,
            "ticket_id": ticket_id,
        },
    )


def _reject_sprint_item_supervisor_read(ctx: RequestContext, sprint_item_id: str) -> None:
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "Sprint Item read is not available to this supervisor",
        {"actor": ctx.actor, "sprint_item_id": sprint_item_id},
    )


def require_direct_write(ctx: RequestContext) -> None:
    """Permit unattributed or explicit Chief direct operations."""
    if ctx.principal.kind in {PrincipalKind.owner, PrincipalKind.chief}:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "direct operation is not available to this actor",
        {"actor": ctx.actor},
    )


def require_feedback_use(conn: sqlite3.Connection, ctx: RequestContext, ticket_id: str) -> None:
    """Permit direct callers, an exact Ticket worker, or its Item supervisor."""
    if ctx.principal.kind in {PrincipalKind.owner, PrincipalKind.chief}:
        return
    if ctx.principal == Principal(PrincipalKind.ticket, ticket_id):
        if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone():
            return
    if ctx.principal.kind is PrincipalKind.sprint_item and _is_current_child_ticket(
        conn, ctx.principal.id, ticket_id
    ):
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "feedback use is not available to this actor",
        {"actor": ctx.actor, "ticket_id": ticket_id},
    )


def require_ticket_worker_write(
    conn: sqlite3.Connection,
    ctx: RequestContext,
) -> None:
    """Permit a direct caller or any exact Ticket-backed Worker."""
    if ctx.principal.kind in {PrincipalKind.owner, PrincipalKind.chief}:
        return
    if ctx.principal.kind is not PrincipalKind.ticket:
        _reject_ticket_worker_write(ctx)

    row = conn.execute(
        "SELECT 1 FROM tickets WHERE id = ?",
        (ctx.principal.id,),
    ).fetchone()
    if row is None:
        _reject_ticket_worker_write(ctx)


def _reject_ticket_worker_write(ctx: RequestContext) -> None:
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "ticket operation is not available to this worker",
        {"actor": ctx.actor},
    )


def require_planning_write(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    capability: PlanningCapability,
) -> None:
    """Permit a direct caller or one exact Ticket-backed planning Worker.

    ``X-Plan-Ticket-ID`` is a truthful local claim, parallel to ``X-Plan-Actor``.
    It is not a token and provides no cryptographic authentication. The stored
    Ticket resolves which planning capability the worker actually has.
    """
    if capability not in PLANNING_CAPABILITIES:
        raise ValueError(f"unknown planning capability: {capability}")
    if ctx.principal.kind in {PrincipalKind.owner, PrincipalKind.chief}:
        return
    if ctx.principal.kind is not PrincipalKind.ticket:
        _reject_planning_write(ctx, capability)

    row = conn.execute(
        "SELECT worker_type FROM tickets WHERE id = ?",
        (ctx.principal.id,),
    ).fetchone()
    if row is None or str(row["worker_type"]) != capability:
        _reject_planning_write(ctx, capability)


def _reject_planning_write(
    ctx: RequestContext,
    capability: PlanningCapability,
) -> None:
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "planning operation is not available to this worker",
        {"actor": ctx.actor, "capability": capability},
    )


def require_chief(ctx: RequestContext) -> None:
    """Require an explicit ``X-Plan-Actor: chief`` identity."""
    if ctx.principal.kind is PrincipalKind.chief:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "chief operation requires the chief actor",
        {"actor": ctx.actor},
    )


def reject_agent_fields(
    ctx: RequestContext,
    body_keys: Iterable[str],
    direct_only_keys: Collection[str],
) -> None:
    """Reject direct-only fields for attributed non-Chief agents."""
    if ctx.principal.kind in {PrincipalKind.owner, PrincipalKind.chief}:
        return
    for key in body_keys:
        if key in direct_only_keys:
            raise PlannerError(
                ErrorCode.agent_forbidden,
                "direct-only field",
                {"field": key, "actor": ctx.actor},
            )
