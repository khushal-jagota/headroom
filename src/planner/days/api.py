"""Day routes (§9). The {date} path segment accepts the literal `today` (resolved
via the planning date) or any ISO date. Thin HTTP shells over the day data layer;
materialize-on-read is spec'd (§3.4)."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter

from planner.core.authctx import require_direct_write, require_planning_write
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.days import actions as days_actions
from planner.days import data as days_data
from planner.days.contracts import AddDayTicketBody
from planner.days.logic import dates
from planner.tickets.api import (
    Cfg,
    Clk,
    ConversationRecord,
    Conversations,
    Ctx,
    DbConn,
    add_conversation_row_signals,
    body_opt_str,
    body_str,
    txn,
)
from planner.tickets.data import read_ticket
from planner.tickets.views import board_view, ticket_json

router = APIRouter()


# --- shared day helpers --------------------------------------------------------


def resolve_day_id(date_seg: str, clock: Clock, config: Config) -> str:
    """`today` resolves through the planning date; any ISO date is parsed then re-formatted
    canonically. Delegates to the shared pure ``dates.resolve_day_id`` (one home, also used by
    the ticket ``--day`` filter) so the rule is not implemented twice."""
    return dates.resolve_day_id(date_seg, clock.now(), config.boundary_hour)


async def _day_view(
    conn: sqlite3.Connection,
    did: str,
    now: int,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    # A2: read_day materializes an absent day (INSERT + event, multi-statement) on an
    # autocommit connection, so this one read is wrapped; the rest run bare.
    with txn(conn):
        day = days_data.read_day(conn, did, now)
    dts = days_data.list_day_tickets(conn, did)
    board = await add_conversation_row_signals(
        board_view(conn, day_id=did), conversations, conversation_record
    )
    cards_by_id = {
        str(card["id"]): card
        for column in board["columns"]
        for card in column["cards"]
    }
    tickets = []
    for dt in dts:
        card = cards_by_id.get(dt.ticket_id)
        if card is None:
            continue
        ticket = ticket_json(read_ticket(conn, dt.ticket_id), now)
        ticket.update(card)
        tickets.append(ticket)
    return {
        "id": day.id,
        "focus": day.focus,
        "brief_take": day.brief_take,
        "watchout": day.watchout,
        "if_today_lands": day.if_today_lands,
        "midday_reconciliation": day.midday_reconciliation,
        "notes": day.notes,
        "created_at": day.created_at,
        "updated_at": day.updated_at,
        "tickets": tickets,
    }


# --- day routes ----------------------------------------------------------------


@router.get("/day/{date}")
async def get_day(
    date: str,
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    did = resolve_day_id(date, clk, cfg)
    return await _day_view(conn, did, clk.now_unix(), conversations, conversation_record)


@router.patch("/day/{date}")
async def patch_day(
    date: str,
    raw: dict[str, Any],
    conn: DbConn,
    ctx: Ctx,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    for field in raw:
        if field not in days_data.DAY_TEXT_FIELDS:
            raise PlannerError(ErrorCode.validation, "unknown day field", {"field": field})
    # Each overview field (and notes) edits on its own — no whole-blob re-serialize.
    edits: dict[str, str] = {}
    for field in days_data.DAY_TEXT_FIELDS:
        value = body_opt_str(raw, field)
        if value is not None:
            edits[field] = value
    if not edits:
        raise PlannerError(ErrorCode.validation, "no day fields to update", {})
    # Validate the complete request above, then keep every field in one write transaction.
    with txn(conn):
        fields = set(edits)
        morning_fields = {"focus", "brief_take", "watchout", "if_today_lands"}
        if fields <= morning_fields:
            require_planning_write(conn, ctx, "planning-day")
        elif fields == {"midday_reconciliation"}:
            require_planning_write(conn, ctx, "planning-midday-check")
        else:
            # Notes and mixed-capability requests stay direct-only.
            require_direct_write(ctx)
        for field, value in edits.items():
            days_data.set_day_field(conn, did, field, value, now)
    return await _day_view(conn, did, now, conversations, conversation_record)


@router.post("/day/{date}/tickets")
async def add_day_ticket(
    date: str,
    raw: dict[str, Any],
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    body = AddDayTicketBody(ticket_id=body_str(raw, "ticket_id"))
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    days_actions.add_ticket_to_day(
        conn,
        did,
        body["ticket_id"],
        now=now,
    )
    return await _day_view(conn, did, now, conversations, conversation_record)


@router.delete("/day/{date}/tickets/{ticket_id}")
async def remove_day_ticket(
    date: str,
    ticket_id: str,
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    did = resolve_day_id(date, clk, cfg)
    now = clk.now_unix()
    days_actions.remove_ticket_from_day(
        conn,
        did,
        ticket_id,
        now=now,
    )
    return await _day_view(conn, did, now, conversations, conversation_record)
