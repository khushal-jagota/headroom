from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import cast

from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import ConversationSystem
from planner.conversation.storage import ConversationStore
from planner.core.contracts import CHIEF_PRINCIPAL, JsonDict
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, TicketEdit
from planner.work_attention import add_work_attention


class _ConversationFacts:
    def __init__(self, *, running: bool = False) -> None:
        self.running = running

    async def is_running(self, conversation_id: str) -> bool:
        return self.running

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return False

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return False


def test_ticket_attention_combines_ownership_proposal_and_conversation_facts(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "attention.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Attention",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_attention', 'codex', '/tmp/workspace', 'full', 2, 1)"
    )
    conn.executemany(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) VALUES "
        "('c_attention', ?, ?, ?, 2)",
        (
            (1, "message_to_owner", "{}"),
            (2, "turn_ended", json.dumps({"ending": "failed"})),
        ),
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = 'c_attention', "
        "pending_proposal = '{}' WHERE id = ?",
        (ticket.id,),
    )
    row: JsonDict = {"id": ticket.id}

    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts()),
            ConversationStore(str(db_path)),
            tickets=[row],
        )
    )

    assert row == {
        "id": ticket.id,
        "awaiting_reply": True,
        "awaiting_approval": True,
        "assigned": True,
        "agent_state": "errored",
    }

    conn.execute(
        "UPDATE conversations SET owner_read_through_sequence = 2 "
        "WHERE conversation_id = 'c_attention'"
    )
    read_row: JsonDict = {"id": ticket.id}
    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts()),
            ConversationStore(str(db_path)),
            tickets=[read_row],
        )
    )
    assert read_row["awaiting_reply"] is False
    assert read_row["agent_state"] == "errored"

    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_attention', 3, 'prompt', '{}', 3)"
    )
    conn.execute(
        "UPDATE conversations SET latest_sequence = 3 WHERE conversation_id = 'c_attention'"
    )
    replied_row: JsonDict = {"id": ticket.id}
    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts()),
            ConversationStore(str(db_path)),
            tickets=[replied_row],
        )
    )
    assert replied_row["agent_state"] == "idle"

    running_row: JsonDict = {"id": ticket.id}
    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts(running=True)),
            ConversationStore(str(db_path)),
            tickets=[running_row],
        )
    )
    assert running_row["awaiting_reply"] is False
    assert running_row["agent_state"] == "working"

    conn.execute(
        'UPDATE tickets SET ceiling_holder=\'{"id":"chief","kind":"chief"}\' WHERE id=?',
        (ticket.id,),
    )
    non_owner_row: JsonDict = {"id": ticket.id}
    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts()),
            ConversationStore(str(db_path)),
            tickets=[non_owner_row],
        )
    )
    assert non_owner_row["awaiting_approval"] is False
    conn.close()


def _attention(conn: sqlite3.Connection, ticket_id: str, notification_type: str) -> bool:
    row = conn.execute(
        "SELECT active FROM notification_attention_state "
        "WHERE subject_kind='ticket' AND subject_id=? AND notification_type=?",
        (ticket_id, notification_type),
    ).fetchone()
    return row is not None and bool(row["active"])


def test_setting_the_ceiling_re_derives_who_the_ticket_is_waiting_on(tmp_path: Path) -> None:
    """Attention reads the ceiling holder, so an edit that moves it must recapture.

    The ceiling used to have its own operation, which ended at the attention capture. It
    is a field edit now, so the edit carries that capture itself.
    """
    conn = connect(str(tmp_path / "ceiling-attention.db"))
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Ceiling attention",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=2,
        edited_body=None,
        next_ceiling="needs_success_condition",
        next_holder=CHIEF_PRINCIPAL,
    )
    # Forget what the Ticket projected, so only this edit can write it back.
    conn.execute(
        "DELETE FROM notification_attention_state WHERE subject_kind='ticket' AND subject_id=?",
        (ticket.id,),
    )

    edit: TicketEdit = {"ceiling": "needs_what_changes"}
    edited = tickets_data.edit_ticket(
        conn,
        ticket.id,
        edit=edit,
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    assert edited.ceiling == "needs_what_changes"
    # How far the Ticket may go moved. Who holds it did not.
    assert edited.ceiling_holder == CHIEF_PRINCIPAL
    assert _attention(conn, ticket.id, "assigned") is False
    assert _attention(conn, ticket.id, "awaiting_approval") is False
    assert _captured_rows(conn, ticket.id) > 0

    # Forget it again, so only the holder edit can write it back.
    conn.execute(
        "DELETE FROM notification_attention_state WHERE subject_kind='ticket' AND subject_id=?",
        (ticket.id,),
    )
    handed: TicketEdit = {"ceiling_holder": OWNER_PRINCIPAL}
    moved = tickets_data.edit_ticket(
        conn,
        ticket.id,
        edit=handed,
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=4,
    )
    assert moved.ceiling_holder == OWNER_PRINCIPAL
    assert moved.ceiling == "needs_what_changes"
    assert _captured_rows(conn, ticket.id) > 0
    conn.close()


def _captured_rows(conn: sqlite3.Connection, ticket_id: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) AS rows FROM notification_attention_state "
            "WHERE subject_kind='ticket' AND subject_id=?",
            (ticket_id,),
        ).fetchone()["rows"]
    )
