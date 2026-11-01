from __future__ import annotations

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast
from unittest.mock import patch

from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import ConversationSystem
from planner.conversation.storage import ConversationStore
from planner.core.clock import RealClock
from planner.core.contracts import JsonDict
from planner.core.db import connect, create_schema
from planner.database_modules import attach
from planner.managers import data as managers_data
from planner.tickets import data as tickets_data
from planner.tickets.api import add_conversation_row_signals
from planner.tickets.contracts import TITLE_MAX_CHARS, PendingTicketProposal
from planner.tickets.logic.fields_codec import proposal_to_json
from planner.tickets.views import board_view


class _ConversationFacts:
    def __init__(self, *, running: bool = False, running_ids: set[str] | None = None) -> None:
        self.running = running
        self.running_ids = running_ids

    async def is_running(self, conversation_id: str) -> bool:
        return conversation_id in self.running_ids if self.running_ids is not None else self.running

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return False

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return False


def test_global_board_and_item_carry_the_same_complete_attention_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "complete_board.db"
    conn = connect(str(db_path))
    create_schema(conn)

    item = managers_data.create_manager(
        conn, title="Complete approval queue", project_id="project_vylo", clock=RealClock()
    )
    rows = [
        tickets_data.create_ticket(
            conn,
            worker_type=worker_type,
            title=title,
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            manager_id=item.id,
        )
        for title, worker_type in (
            ("On Today", "coding"),
            ("Off Today owner approval", "coding"),
            ("Off Today feedback", "exploration"),
            ("Off Today agent approval", "coding"),
        )
    ]
    for index in (1, 3):
        conn.execute(
            "UPDATE tickets SET pending_proposal = ? WHERE id = ?",
            (
                proposal_to_json(PendingTicketProposal("brief", "Review this brief.", "worker", 2)),
                rows[index].id,
            ),
        )
    conn.execute("UPDATE tickets SET stage = 'needs_answer' WHERE id = ?", (rows[2].id,))
    conn.execute(
        "UPDATE tickets SET ceiling_holder = ? WHERE id = ?",
        (json.dumps({"kind": "chief", "id": "chief"}), rows[3].id),
    )
    conn.execute(
        "INSERT INTO conversations "
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_off_today_child', 'codex', '/tmp/workspace', 'full', 1, 1)"
    )
    conn.execute(
        "INSERT INTO conversation_events "
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_off_today_child', 1, 'message_to_owner', '{}', 1)"
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = 'c_off_today_child' WHERE id = ?", (rows[1].id,)
    )
    conn.execute(
        "INSERT INTO conversations "
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES ('c_quiet_supervisor', 'codex', '/tmp/workspace', 'full', 0, 1)"
    )
    conn.execute(
        "UPDATE agents SET conversation_id = 'c_quiet_supervisor' WHERE agent_key = ?",
        (item.manager_agent_key,),
    )
    conn.commit()
    board = board_view(conn)
    record = ConversationStore(str(db_path))
    # Board cards and duplicate nested child rows share one conversation projection.
    with patch.object(record, "attention_facts", wraps=record.attention_facts) as attention:
        asyncio.run(
            add_conversation_row_signals(
                conn,
                board,
                cast(ConversationSystem, _ConversationFacts(running_ids={"c_off_today_child"})),
                record,
            )
        )
    attention.assert_awaited_once_with({"c_off_today_child", "c_quiet_supervisor"})
    assert {card["id"] for column in board["columns"] for card in column["cards"]} == {
        row.id for row in rows
    }
    [item_row] = board["managers"]
    assert item_row["title"] == "Complete approval queue"
    assert "priority" not in item_row
    children = {row["id"]: row for row in item_row["tickets"]}
    assert set(children) == {row.id for row in rows}
    owner_approval = children[rows[1].id]
    assert owner_approval["awaiting_approval"] is True
    assert owner_approval["awaiting_reply"] is True
    assert owner_approval["conversation_signal"] == {"working": True, "message_for_owner": True}
    assert owner_approval["awaiting_agent_approval"] is False
    assert owner_approval["gating_field"] == "brief"
    assert owner_approval["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert owner_approval["worker_type"] == "coding"
    assert owner_approval["blocked"] is False
    assert owner_approval["manager_id"] == item.id
    feedback = children[rows[2].id]
    assert feedback["assigned"] is True
    assert feedback["awaiting_approval"] is False
    assert feedback["has_pending_proposal"] is False
    assert children[rows[3].id]["awaiting_approval"] is False
    assert children[rows[3].id]["awaiting_agent_approval"] is True
    assert item_row["ticket_rollup"]["awaiting_approval"] is True
    assert item_row["ticket_rollup"]["assigned"] is True
    assert item_row["ticket_rollup"]["awaiting_reply"] is True
    assert item_row["awaiting_reply"] is False
    assert item_row["conversation_signal"] == {"working": False, "message_for_owner": False}
    assert all(not children[row.id]["awaiting_reply"] for row in (rows[0], rows[2], rows[3]))
    conn.close()


class _ConversationWaitingOnAnAnswer:
    """A worker that has asked for permission and is waiting on it."""

    async def is_running(self, conversation_id: str) -> bool:
        return True

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return True

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return False


def test_broken_child_does_not_hide_healthy_attention_or_manager_rollup(tmp_path: Path) -> None:
    """The shared attention layer must not parse invalid holders or require missing types."""
    from planner.work_attention import add_work_attention

    db_path = tmp_path / "broken_attention.db"
    conn = connect(str(db_path))
    create_schema(conn)
    item = managers_data.create_manager(
        conn, title="Fault isolation", project_id="project_vylo", clock=RealClock()
    )
    healthy, broken = [
        tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title=title,
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            manager_id=item.id,
        )
        for title in ("Healthy approval", "Broken child")
    ]
    conn.execute(
        "UPDATE tickets SET pending_proposal=? WHERE id=?",
        (proposal_to_json(PendingTicketProposal("brief", "Review this.", "worker", 2)), healthy.id),
    )
    with closing(sqlite3.connect(db_path)) as raw:
        attach(raw)
        raw.execute("BEGIN")
        raw.execute(
            "UPDATE tickets SET worker_type='missing_type',ceiling_holder=? WHERE id=?",
            (json.dumps({"kind": "ticket", "id": "missing_holder"}), broken.id),
        )
        raw.commit()
    tickets_data.audit_ticket_registry_integrity(conn, startup=True)
    ticket_rows: list[JsonDict] = [{"id": healthy.id}, {"id": broken.id}]
    manager_rows: list[JsonDict] = [{"id": item.id}]
    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts()),
            ConversationStore(str(db_path)),
            tickets=ticket_rows,
            managers=manager_rows,
        )
    )
    assert ticket_rows[0]["awaiting_approval"] is True
    assert ticket_rows[1]["agent_state"] == "errored"
    assert ticket_rows[1]["assigned"] is False
    assert ticket_rows[1]["awaiting_approval"] is False
    assert manager_rows[0]["ticket_rollup"]["awaiting_approval"] is True
    assert manager_rows[0]["ticket_rollup"]["agent_state"] == "errored"
    assert conn.execute("SELECT ceiling_holder FROM tickets WHERE id=?", (broken.id,)).fetchone()[
        0
    ] == json.dumps({"kind": "ticket", "id": "missing_holder"})
    conn.close()
