"""Day routes (§9). The {date} path segment accepts the literal `today` (resolved
via the planning date) or any ISO date. Thin HTTP shells over the day data layer;
materialize-on-read is spec'd (§3.4)."""

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
from planner.days.contracts import AddDayTicketBody
from planner.days.logic.dates import planning_date
from planner.tickets.api import Cfg, Clk, Ctx, DbConn, body_opt_str, body_str, txn
from planner.tickets.data import read_ticket
from planner.tickets.views import ticket_json

router = APIRouter()


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
        "chat_session_key": day.chat_session_key,
        "created_at": day.created_at,
        "updated_at": day.updated_at,
        "tickets": [ticket_json(read_ticket(conn, dt.ticket_id), now) for dt in dts],
    }


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
