"""§7.6 request context and write validation. This is the server-side dependency
that T10/T13 route bodies consume: it reads the X-Plan-* headers, classifies each
request as a claimed agent, a plain agent, or the human, and validates a presented
claim against a ticket's active lease. Lease liveness is decided only by
dispatch.logic.claims — never re-derived here.

FastAPI appears here because this is part of the server shell (the server.py
family), not a logic or data module."""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from typing import Final, NoReturn

from fastapi import Request

from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.dispatch.logic.claims import has_active_claim, is_expired

X_PLAN_RUN_ID: Final = "X-Plan-Run-Id"
X_PLAN_CLAIM: Final = "X-Plan-Claim"
X_PLAN_ACTOR: Final = "X-Plan-Actor"
_DEFAULT_AGENT_ACTOR: Final = "agent"    # §7.6: PLAN_ACTOR default "agent"
_HUMAN_ACTOR: Final = "human"


@dataclass(frozen=True)
class RequestContext:
    actor: str
    run_id: str | None
    claim: str | None
    is_claimed_agent: bool
    is_human: bool


def _normalize(raw: str | None) -> str | None:
    """strip(); an empty or whitespace-only header is an absent header."""
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _classify(run_id: str | None, claim: str | None, actor: str | None) -> RequestContext:
    """Pure classification over the §1.1 truth table; inputs are pre-normalized."""
    if run_id is not None or claim is not None:
        # Any claim header present (whole or half) is a claimed-agent attempt.
        return RequestContext(
            actor=actor if actor is not None else _DEFAULT_AGENT_ACTOR,
            run_id=run_id,
            claim=claim,
            is_claimed_agent=True,
            is_human=False,
        )
    if actor is not None:
        # Non-dispatched agent context (planner-main chat agent): proposer from PLAN_ACTOR.
        return RequestContext(
            actor=actor,
            run_id=None,
            claim=None,
            is_claimed_agent=False,
            is_human=False,
        )
    # The human: UI requests carry no agent headers.
    return RequestContext(
        actor=_HUMAN_ACTOR,
        run_id=None,
        claim=None,
        is_claimed_agent=False,
        is_human=True,
    )


def request_context(request: Request) -> RequestContext:
    """FastAPI dependency: reads the three headers, normalizes, classifies. No DB, no clock."""
    headers = request.headers
    return _classify(
        _normalize(headers.get(X_PLAN_RUN_ID)),
        _normalize(headers.get(X_PLAN_CLAIM)),
        _normalize(headers.get(X_PLAN_ACTOR)),
    )


def _reject_stale(
    reason: str,
    message: str,
    ctx: RequestContext,
    ticket_id: str,
    active_claim_present: bool,
    stored_lock: str | None,
    extra: JsonDict | None = None,
) -> NoReturn:
    # Presented values are echoed back — except a presented token equal to the stored
    # claim_lock (the expired/run_mismatch/missing-half paths reach here with a correct
    # token): verbatim it would put the live stored token into a detail that may be
    # logged or displayed, so it is redacted.
    presented_claim = ctx.claim
    if presented_claim is not None and presented_claim == stored_lock:
        presented_claim = "(redacted)"
    detail: JsonDict = {
        "reason": reason,
        "ticket_id": ticket_id,
        "presented_run_id": ctx.run_id,
        "presented_claim": presented_claim,
        "active_claim_present": active_claim_present,
    }
    if extra is not None:
        detail.update(extra)
    raise PlannerError(ErrorCode.stale_claim, message, detail)


def require_claim(
    conn: sqlite3.Connection, ctx: RequestContext, ticket_id: str, now: int
) -> None:
    """§7.6 write validation. Checks run in the fixed order (ticket exists → active
    claim → unexpired → token → run id); the first failure raises stale_claim naming
    which check failed. The stored token is never echoed. now is unix seconds."""
    row = conn.execute(
        "SELECT claim_lock, claim_expires FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "ticket not found", {"ticket_id": ticket_id})
    claim_lock = str(row["claim_lock"]) if row["claim_lock"] is not None else None
    claim_expires = int(row["claim_expires"]) if row["claim_expires"] is not None else None
    active = has_active_claim(claim_lock, claim_expires, now)

    if ctx.run_id is None or ctx.claim is None:
        missing = [
            name
            for name, value in ((X_PLAN_RUN_ID, ctx.run_id), (X_PLAN_CLAIM, ctx.claim))
            if value is None
        ]
        _reject_stale(
            "missing_header",
            "claim headers incomplete",
            ctx,
            ticket_id,
            active,
            claim_lock,
            {"missing": missing},
        )
    if claim_lock is None:
        _reject_stale(
            "none_active", "ticket has no active claim", ctx, ticket_id, active, claim_lock
        )
    if is_expired(claim_expires, now):
        _reject_stale(
            "expired",
            "claim expired",
            ctx,
            ticket_id,
            active,
            claim_lock,
            {"claim_expires": claim_expires},
        )
    if ctx.claim != claim_lock:
        _reject_stale(
            "foreign",
            "claim token does not match the ticket's active claim",
            ctx,
            ticket_id,
            active,
            claim_lock,
        )
    run_row = conn.execute(
        "SELECT id FROM runs WHERE ticket_id=? AND status='running' "
        "ORDER BY started_at DESC LIMIT 1",
        (ticket_id,),
    ).fetchone()
    if run_row is None or str(run_row["id"]) != ctx.run_id:
        _reject_stale(
            "run_mismatch",
            "run id does not match the ticket's running run",
            ctx,
            ticket_id,
            active,
            claim_lock,
        )
    return None


def validate_carried_claim(
    conn: sqlite3.Connection, ctx: RequestContext, ticket_id: str, now: int
) -> None:
    """§7.6 gate for mutating ticket-target routes: a request carrying claim headers
    must hold the target ticket's active claim (the full require_claim order applies);
    human and plain-agent requests pass through unchanged."""
    if ctx.is_claimed_agent:
        require_claim(conn, ctx, ticket_id, now)
    return None


def reject_agents(ctx: RequestContext) -> None:
    """(H) routes: any agent-classified request (claimed or plain) is agent_forbidden."""
    if ctx.is_human:
        return None
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "human-only action",
        {"actor": ctx.actor, "is_claimed_agent": ctx.is_claimed_agent},
    )


def reject_agent_fields(
    ctx: RequestContext, body_keys: Iterable[str], human_only_keys: Collection[str]
) -> None:
    """Field-level agent boundary for PATCH routes that expose both agent-permitted and
    human-only keys (§8, §14): an agent-classified request (claimed or plain) that
    touches any human-only field is agent_forbidden, naming the first offending field.
    Human requests pass through untouched."""
    if ctx.is_human:
        return None
    for key in body_keys:
        if key in human_only_keys:
            raise PlannerError(
                ErrorCode.agent_forbidden,
                "human-only field",
                {
                    "field": key,
                    "actor": ctx.actor,
                    "is_claimed_agent": ctx.is_claimed_agent,
                },
            )
    return None
