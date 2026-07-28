"""Truthful request identity and the direct-write authority boundary.

No ``X-Plan-Actor`` header means the request is unattributed. ``chief`` identifies
the Panels Chief. Every other non-empty actor value identifies an attributed
non-Chief agent. Direct product operations permit unattributed and Chief requests;
worker identities remain subject to the existing direct-write restrictions except
for the narrow, Ticket-backed planning capabilities defined here.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Final, Literal

from fastapi import Request

from planner.core.errors import ErrorCode, PlannerError

X_PLAN_ACTOR: Final = "X-Plan-Actor"
X_PLAN_TICKET_ID: Final = "X-Plan-Ticket-ID"
PLAN_ACTOR_SCOPE_KEY: Final = "planner.request_actor"
PLAN_TICKET_ID_SCOPE_KEY: Final = "planner.request_ticket_id"
_UNATTRIBUTED_ACTOR: Final = "unattributed"
_CHIEF_ACTOR: Final = "chief"
_WORKER_ACTOR: Final = "worker"

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
    actor: str
    is_attributed: bool
    is_chief: bool
    ticket_id: str | None = None


def _normalize(raw: str | None) -> str | None:
    """Strip the header; whitespace-only values are absent."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _classify(actor: str | None, ticket_id: str | None = None) -> RequestContext:
    normalized_ticket_id = _normalize(ticket_id)
    if actor is None:
        return RequestContext(
            actor=_UNATTRIBUTED_ACTOR,
            is_attributed=False,
            is_chief=False,
            ticket_id=normalized_ticket_id,
        )
    return RequestContext(
        actor=actor,
        is_attributed=True,
        is_chief=actor == _CHIEF_ACTOR,
        ticket_id=normalized_ticket_id,
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
    return _classify(_normalize(actor), _normalize(ticket_id))


def require_direct_write(ctx: RequestContext) -> None:
    """Permit unattributed or explicit Chief direct operations."""
    if not ctx.is_attributed or ctx.is_chief:
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "direct operation is not available to this actor",
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
    if not ctx.is_attributed or ctx.is_chief:
        return
    if ctx.actor != _WORKER_ACTOR or ctx.ticket_id is None:
        _reject_planning_write(ctx, capability)

    row = conn.execute(
        "SELECT worker_type FROM tickets WHERE id = ?",
        (ctx.ticket_id,),
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
    if ctx.is_chief:
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
    if not ctx.is_attributed or ctx.is_chief:
        return
    for key in body_keys:
        if key in direct_only_keys:
            raise PlannerError(
                ErrorCode.agent_forbidden,
                "direct-only field",
                {"field": key, "actor": ctx.actor},
            )
