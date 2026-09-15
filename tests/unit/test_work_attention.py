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
        "ticket_status = 'awaiting_approval', pending_proposal = '{}' WHERE id = ?",
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
    conn.close()
