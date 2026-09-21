"""A message the owner has not read reaches the Workspace rail off its Day.

The Day is the rail's roster. That is right for work he scheduled, and wrong for a
message: he has not seen it, so a Day he did not put its Ticket on cannot be his answer.
These tests hold the two halves of that — which conversations hold an unread message,
and which Tickets the board therefore returns — and hold the Day rule for everything
else.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ResolvedConversationStart,
)
from planner.conversation.events import MessageToOwnerEventPayload
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.days.data import add_day_ticket
from planner.tickets.data import create_ticket
from planner.tickets.views import board_view

A_DAY = "2026-09-20"


def _card_ids(board: dict[str, object]) -> set[str]:
    columns = board["columns"]
    assert isinstance(columns, list)
    return {
        str(card["id"])
        for column in columns
        for card in column["cards"]
    }


def _make_ticket(conn: Connection, title: str) -> str:
    ticket = create_ticket(
        conn,
        title=title,
        principal=OWNER_PRINCIPAL,
        now=1,
        worker_type="coding",
        title_max_chars=200,
    )
    return ticket.id


# --- which conversations hold one -------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> ConversationStore:
    db_path = tmp_path / "conversations.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    return ConversationStore(str(db_path), integer_now=lambda: 1_700_000_000)


def _resolved(conversation_id: str) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id=conversation_id,
        backend_key=ConversationBackendKey.hermes,
        model="a-model",
        reasoning_effort=None,
        role_materials=None,
        workspace_folder=Path("/tmp/workspace"),
        access=ConversationAccess.full,
    )


def _a_message_to_the_owner() -> MessageToOwnerEventPayload:
    return MessageToOwnerEventPayload(
        content=text_message_content("ready for you"),
        sender=Principal(PrincipalKind.ticket, "t_one"),
        recipient=OWNER_PRINCIPAL,
        sender_label="Worker",
    )


def test_only_conversations_past_the_owners_read_mark_are_named(
    store: ConversationStore,
) -> None:
    async def exercise() -> None:
        for conversation_id in ("unread", "read", "quiet"):
            await store.create_conversation(_resolved(conversation_id))
        await store.append_event("unread", _a_message_to_the_owner())
        await store.append_event("read", _a_message_to_the_owner())
        await store.advance_owner_read_through_sequence("read", 1)

        named = await store.conversation_ids_holding_unread_owner_message()

        assert named == frozenset({"unread"})

    asyncio.run(exercise())


# --- which Tickets the board returns ----------------------------------------------


def test_a_named_ticket_joins_the_board_from_off_the_day(tmp_db: Connection) -> None:
    on_day = _make_ticket(tmp_db, "On today")
    off_day_with_a_message = _make_ticket(tmp_db, "Off today, message waiting")
    off_day_quiet = _make_ticket(tmp_db, "Off today, nothing waiting")
    add_day_ticket(tmp_db, A_DAY, on_day, 1)

    board = board_view(
        tmp_db,
        day_id=A_DAY,
        ticket_ids_holding_unread_owner_message=[off_day_with_a_message],
    )

    assert _card_ids(board) == {on_day, off_day_with_a_message}
    assert off_day_quiet not in _card_ids(board)


def test_a_named_ticket_already_on_the_day_appears_once(tmp_db: Connection) -> None:
    on_day = _make_ticket(tmp_db, "On today, message waiting")
    add_day_ticket(tmp_db, A_DAY, on_day, 1)

    board = board_view(
        tmp_db,
        day_id=A_DAY,
        ticket_ids_holding_unread_owner_message=[on_day, on_day],
    )

    columns = board["columns"]
    assert isinstance(columns, list)
    cards = [card for column in columns for card in column["cards"]]
    assert [str(card["id"]) for card in cards] == [on_day]
