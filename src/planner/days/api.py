"""Day routes (§9). The {date} path segment accepts the literal `today` (resolved
via the planning date) or any ISO date. Thin HTTP shells over the day data layer;
materialize-on-read is spec'd (§3.4)."""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter

from planner.core import authority
from planner.core.authority import require_above
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.days.logic import dates
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.tickets.api import (
    Cfg,
    Clk,
    ConversationRecord,
    Conversations,
    Ctx,
    DbConn,
    add_conversation_row_signals,
    body_opt_str,
    txn,
)
from planner.tickets.contracts import TicketListFilters
from planner.tickets.data import read_ticket
from planner.tickets.views import board_view, list_ticket_summaries, ticket_json

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
        conn, board_view(conn, day_id=did), conversations, conversation_record
    )
    cards_by_id = {str(card["id"]): card for column in board["columns"] for card in column["cards"]}
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
        # Which Day fields a planning Worker type may write is that type's own
        # declaration, in planner.core.authority.declarations, not a branch here. A
        # request mixing a declared field with an undeclared one was never a planning
        # request and is refused as a whole, exactly as the branches did.
        require_above(conn, ctx.principal, authority.plan("day", *sorted(edits)))
        for field, value in edits.items():
            days_data.set_day_field(conn, did, field, value, now)
    return await _day_view(conn, did, now, conversations, conversation_record)


@router.get("/day/{date}/tickets")
async def get_day_ticket_summaries(
    date: str,
    conn: DbConn,
    cfg: Cfg,
    clk: Clk,
    limit: int = DEFAULT_LIST_LIMIT,
    offset: int = 0,
) -> JsonDict:
    did = resolve_day_id(date, clk, cfg)
    with txn(conn):
        days_data.read_day(conn, did, clk.now_unix())
    page = list_ticket_summaries(
        conn,
        page_request=ListPageRequest(limit=limit, offset=offset),
        filters=TicketListFilters(include_terminal=True),
        project_id=None,
        sprint_id=None,
        sprint_item_id=None,
        day_id=did,
        day_order=True,
    )
    response = page.response("tickets")
    response["id"] = did
    return response

