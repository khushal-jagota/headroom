"""Read-only historical Day routes."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter

from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.days import data as days_data
from planner.days.logic import dates
from planner.list_reads.configuration import DEFAULT_LIST_LIMIT
from planner.list_reads.contracts import ListPageRequest
from planner.tickets.api import (
    Cfg,
    Clk,
    ConversationRecord,
    Conversations,
    DbConn,
    add_conversation_row_signals,
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
    day = days_data.read_day(conn, did, now)
    dts = days_data.list_day_tickets(conn, did)
    board = await add_conversation_row_signals(
        conn,
        board_view(conn, ticket_ids_holding_unread_owner_message=tuple(dt.ticket_id for dt in dts)),
        conversations,
        conversation_record,
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
