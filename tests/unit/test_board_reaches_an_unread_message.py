"""Global discovery retains unread messages and includes ordinary off-Day work."""

from __future__ import annotations

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
from planner.tickets.data import create_ticket

A_DAY = "2026-09-20"


def _card_ids(board: dict[str, object]) -> set[str]:
    columns = board["columns"]
    assert isinstance(columns, list)
    return {str(card["id"]) for column in columns for card in column["cards"]}


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




# --- which Tickets the board returns ----------------------------------------------
