"""Day routes (§9). The {date} path segment accepts the literal `today` (resolved
via the planning date) or any ISO date. Thin HTTP shells over the day data layer and
the pure plan-tree transforms: materialize-on-read is spec'd (§3.4), plan mutations
persist the transform result then (for invalidate) enqueue exactly one replan onto
the T11 latest-wins queue AFTER the commit."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter

from planner.core import ids
from planner.core.authctx import reject_agents, validate_carried_claim
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.days.contracts import AddDayTicketBody, PlanNodeBody, PlanTree
from planner.days.logic import tree as plan_tree
from planner.days.logic.dates import planning_date
from planner.days.scheduler import submit_replan
from planner.tickets.api import Cfg, Clk, Ctx, DbConn, body_opt_str, body_str, txn
from planner.tickets.data import read_ticket
from planner.tickets.views import ticket_json

router = APIRouter()


# --- request-body marshallers (contract shapes in days/contracts.py) ------------


def _marshal_plan_node(raw: JsonDict) -> PlanNodeBody:
    node = raw.get("node")
    if node is not None and not isinstance(node, int | str):
        raise PlannerError(
            ErrorCode.validation, "node must be 'root' or a child position", {"node": node}
        )
    return PlanNodeBody(node=node)


# --- shared day helpers --------------------------------------------------------


def resolve_day_id(date_seg: str, clock: Clock, config: Config) -> str:
    """Amendment 10: `today` resolves through the planning date. A6: the ISO branch
    parses then re-formats via ids.day_id, so compact forms ('20260704') still yield
    canonical day_2026-07-04 ids — the raw segment is never string-formatted."""
    if date_seg == "today":
        return ids.day_id(planning_date(clock.now(), config.boundary_hour))
    try:
        parsed = date.fromisoformat(date_seg)
    except ValueError:
        raise PlannerError(ErrorCode.validation, "invalid date", {"date": date_seg}) from None
    return ids.day_id(parsed)


def _day_view(conn: sqlite3.Connection, did: str, now: int) -> JsonDict:
    # A2: read_day materializes an absent day (INSERT + event, multi-statement) on an
    # autocommit connection, so this one read is wrapped; the rest run bare.
    with txn(conn):
        day = days_data.read_day(conn, did, now)
    dts = days_data.list_day_tickets(conn, did)
    return {
        "id": day.id,
        "focus": day.focus,
        "brief_take": day.brief_take,
        "watchout": day.watchout,
        "if_today_lands": day.if_today_lands,
        "notes": day.notes,
        "plan": plan_tree.tree_to_dict(day.plan) if day.plan is not None else None,
        "chat_session_key": day.chat_session_key,
        "created_at": day.created_at,
        "updated_at": day.updated_at,
        "tickets": [ticket_json(read_ticket(conn, dt.ticket_id), now) for dt in dts],
    }


def _parse_node(raw: object) -> str | int:
    if raw == "root":
        return "root"
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    raise PlannerError(
        ErrorCode.validation, "node must be 'root' or a child position", {"node": raw}
    )


def _load_plan_or_error(conn: sqlite3.Connection, did: str) -> PlanTree:
    plan = days_data.load_plan(conn, did)
    if plan is None:
        raise PlannerError(ErrorCode.validation, "day has no plan", {"day_id": did})
    return plan


def _require_child(tree: PlanTree, position: int) -> None:
    if not any(child.position == position for child in tree.children):
        raise PlannerError(ErrorCode.validation, "no such plan node", {"node": position})


# --- day routes ----------------------------------------------------------------


@router.get("/day/{date}")
async def get_day(date: str, conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    did = resolve_day_id(date, clk, cfg)
    return _day_view(conn, did, clk.now_unix())


@router.patch("/day/{date}")
async def patch_day(date: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, cfg: Cfg,
                    clk: Clk) -> JsonDict:
    reject_agents(ctx)  # §8: overview fields + notes are human-only; the boundary writes its own
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    # Each overview field (and notes) edits on its own — no whole-blob re-serialize.
    edits: dict[str, str] = {}
    for field in days_data.DAY_TEXT_FIELDS:
        value = body_opt_str(raw, field)
        if value is not None:
            edits[field] = value
    if not edits:
        raise PlannerError(ErrorCode.validation, "no day fields to update", {})
    for field, value in edits.items():
        with txn(conn):
            days_data.set_day_field(conn, did, field, value, now)
    return _day_view(conn, did, now)


@router.post("/day/{date}/tickets")
async def add_day_ticket(date: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx,
                         cfg: Cfg, clk: Clk) -> JsonDict:
    body = AddDayTicketBody(ticket_id=body_str(raw, "ticket_id"))
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    read_ticket(conn, body["ticket_id"])  # existence guard (avoids a raw FK 500)
    validate_carried_claim(conn, ctx, body["ticket_id"], now)
    with txn(conn):
        days_data.add_day_ticket(conn, did, body["ticket_id"], now)
    return _day_view(conn, did, now)


@router.delete("/day/{date}/tickets/{ticket_id}")
async def remove_day_ticket(date: str, ticket_id: str, conn: DbConn, ctx: Ctx, cfg: Cfg,
                            clk: Clk) -> JsonDict:
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    validate_carried_claim(conn, ctx, ticket_id, now)
    with txn(conn):
        days_data.remove_day_ticket(conn, did, ticket_id, now)
    return _day_view(conn, did, now)


@router.post("/day/{date}/plan/accept")
async def plan_accept(date: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, cfg: Cfg,
                      clk: Clk) -> JsonDict:
    body = _marshal_plan_node(raw)
    reject_agents(ctx)
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    node = _parse_node(body["node"])
    tree = _load_plan_or_error(conn, did)
    if isinstance(node, int):
        _require_child(tree, node)
    new_tree, effects = plan_tree.accept_node(tree, node)
    with txn(conn):
        days_data.apply_plan_effects(conn, did, new_tree, effects, now)
    return _day_view(conn, did, now)


@router.post("/day/{date}/plan/accept-all")
async def plan_accept_all(date: str, conn: DbConn, ctx: Ctx, cfg: Cfg, clk: Clk) -> JsonDict:
    reject_agents(ctx)
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    tree = _load_plan_or_error(conn, did)
    new_tree, effects = plan_tree.accept_all(tree)
    with txn(conn):
        days_data.apply_plan_effects(conn, did, new_tree, effects, now)
    return _day_view(conn, did, now)


@router.post("/day/{date}/plan/invalidate")
async def plan_invalidate(date: str, raw: dict[str, Any], conn: DbConn, ctx: Ctx, cfg: Cfg,
                          clk: Clk) -> JsonDict:
    body = _marshal_plan_node(raw)
    reject_agents(ctx)
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    node = _parse_node(body["node"])
    tree = _load_plan_or_error(conn, did)
    if isinstance(node, int):
        _require_child(tree, node)
        new_tree, effects = plan_tree.invalidate_child(tree, node)
    else:
        new_tree, effects = plan_tree.invalidate_root(tree)
    with txn(conn):
        replans = days_data.apply_plan_effects(conn, did, new_tree, effects, now)
    for request in replans:  # post-commit enqueue: the R5 consumer sees committed state only
        submit_replan(did, request)
    return _day_view(conn, did, now)


@router.post("/day/{date}/plan/reject-all")
async def plan_reject_all(date: str, conn: DbConn, ctx: Ctx, cfg: Cfg, clk: Clk) -> JsonDict:
    reject_agents(ctx)
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    tree = _load_plan_or_error(conn, did)
    new_tree, effects = plan_tree.reject_all(tree)
    with txn(conn):
        days_data.apply_plan_effects(conn, did, new_tree, effects, now)
    return _day_view(conn, did, now)
