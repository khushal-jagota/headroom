from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast

from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import ConversationSystem
from planner.conversation.storage import ConversationStore
from planner.core.contracts import JsonDict
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS
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

    # A coding Brief is the worker's Stage, so the Ticket is never named as his own work.
    assert row == {
        "id": ticket.id,
        "awaiting_reply": True,
        "awaiting_answer": False,
        "awaiting_approval": True,
        "awaiting_agent_approval": False,
        "assigned": False,
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
    # The ceiling moved to the chief, so the same parked proposal is the other half of
    # the split: never the owner's queue, and named as an agent's on every route.
    assert non_owner_row["awaiting_approval"] is False
    assert non_owner_row["awaiting_agent_approval"] is True
    conn.close()


def test_only_a_user_owned_stage_is_the_owners_work(tmp_path: Path) -> None:
    """The Brief does not make a Ticket the owner's. The Stage's ownership does."""
    db_path = tmp_path / "assignment.db"
    conn = connect(str(db_path))
    create_schema(conn)
    worker_owned = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="A worker writes this Brief",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    user_owned = tickets_data.create_ticket(
        conn,
        worker_type="personal",
        title="Khushal writes this Brief",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    assert worker_owned.stage == user_owned.stage == "needs_brief"

    rows: list[JsonDict] = [{"id": worker_owned.id}, {"id": user_owned.id}]
    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts()),
            ConversationStore(str(db_path)),
            tickets=rows,
        )
    )

    assigned_by_id = {str(row["id"]): row["assigned"] for row in rows}
    assert assigned_by_id == {worker_owned.id: False, user_owned.id: True}
    conn.close()


class _ConversationWaitingOnAnAnswer:
    """A worker that has asked for permission and is waiting on it."""

    async def is_running(self, conversation_id: str) -> bool:
        return True

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return True

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return False


def test_a_waiting_ask_is_not_reported_as_a_message(tmp_path: Path) -> None:
    """The screens say what the worker is waiting for, and an ask is not a message.

    Both facts were OR-ed into `awaiting_reply`, so every rail row, Sprint Item row and
    roster row called a pending ask a message.
    """
    db_path = tmp_path / "answer.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Waiting on an answer",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_answer', 'codex', '/tmp/workspace', 'full', 0, 1)"
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = 'c_answer' WHERE id = ?", (ticket.id,)
    )
    row: JsonDict = {"id": ticket.id}

    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationWaitingOnAnAnswer()),
            ConversationStore(str(db_path)),
            tickets=[row],
        )
    )

    assert row["awaiting_answer"] is True
    assert row["awaiting_reply"] is False
    assert row["agent_state"] == "working"
