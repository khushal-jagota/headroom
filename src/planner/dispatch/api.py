"""Run routes (§9): heartbeat and close. Both require the claim headers — a request
with no claim headers fails inside require_claim with stale_claim/missing_header.
require_claim takes a ticket id, so each route resolves run -> ticket_id first via the
run reader homed in tickets/views. close restricts the outcome to the agent-reportable
set (§8); the writer itself also admits the failure statuses reserved for T11's
runtime, so the restriction is a membership test against the contract constant, not a
re-implementation of close_run's own check."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from planner.core.authctx import require_claim
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.dispatch import data as dispatch_data
from planner.dispatch.contracts import AGENT_CLOSE_OUTCOMES, RunStatus
from planner.tickets import views as tickets_views
from planner.tickets.api import Cfg, Clk, Ctx, DbConn, parse_enum, txn

router = APIRouter()


class CloseRunBody(BaseModel):
    outcome: str = ""
    summary: str | None = None


@router.post("/runs/{run_id}/heartbeat")
async def heartbeat(run_id: str, conn: DbConn, ctx: Ctx, cfg: Cfg, clk: Clk) -> JsonDict:
    now = clk.now_unix()
    run = tickets_views.get_run(conn, run_id)
    if run is None:
        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
    require_claim(conn, ctx, run["ticket_id"], now)
    with txn(conn):
        new_expires = dispatch_data.heartbeat(conn, run_id, now, cfg.claim_ttl_seconds)
    return {"run": tickets_views.get_run(conn, run_id), "claim_expires": new_expires}


@router.post("/runs/{run_id}/close")
async def close_run(run_id: str, body: CloseRunBody, conn: DbConn, ctx: Ctx, cfg: Cfg,
                    clk: Clk) -> JsonDict:
    now = clk.now_unix()
    run = tickets_views.get_run(conn, run_id)
    if run is None:
        raise PlannerError(ErrorCode.not_found, "run not found", {"run_id": run_id})
    require_claim(conn, ctx, run["ticket_id"], now)
    status = parse_enum(RunStatus, body.outcome, "outcome")
    if status not in AGENT_CLOSE_OUTCOMES:
        raise PlannerError(
            ErrorCode.validation, "outcome must be done or blocked", {"outcome": body.outcome}
        )
    with txn(conn):
        dispatch_data.close_run(conn, run_id, status, now, cfg.failure_limit, summary=body.summary)
    return {"run": tickets_views.get_run(conn, run_id)}
