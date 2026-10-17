from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.retired_workers import seed_historical_worker_type

from planner.conversation.contracts import ConversationSystem
from planner.conversation.storage import ConversationAttentionFacts, ConversationStore
from planner.core.clock import RealClock
from planner.core.contracts import JsonDict
from planner.core.db import connect, create_schema
from planner.days.data import add_day_ticket
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.api import add_conversation_row_signals
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.views import board_view
from planner.work_attention import add_work_attention
from planner.worker_settings.api import add_agent_conversation_signals


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
        "UPDATE tickets SET conversation_id = 'c_attention', pending_proposal = '{}' WHERE id = ?",
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


def test_global_board_and_item_carry_the_same_complete_attention_queue(tmp_path: Path) -> None:
    db_path = tmp_path / "complete_board.db"
    conn = connect(str(db_path))
    create_schema(conn)
    item = sprints_data.create_item(
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
            sprint_item_id=item.id,
        )
        for title, worker_type in (
            ("On Today", "coding"),
            ("Off Today owner approval", "coding"),
            ("Off Today feedback", "exploration"),
            ("Off Today agent approval", "coding"),
        )
    ]
    add_day_ticket(conn, "2026-10-16", rows[0].id, 1)
    for index in (1, 3):
        conn.execute("UPDATE tickets SET pending_proposal = '{}' WHERE id = ?", (rows[index].id,))
    conn.execute("UPDATE tickets SET stage = 'needs_answer' WHERE id = ?", (rows[2].id,))
    conn.execute(
        "UPDATE tickets SET ceiling_holder = ? WHERE id = ?",
        (json.dumps({"kind": "chief", "id": "chief"}), rows[3].id),
    )
    conn.execute("UPDATE sprint_items SET priority = 'P0' WHERE id = ?", (item.id,))
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
    conn.commit()
    board = board_view(conn)
    record = ConversationStore(str(db_path))
    # Board cards and duplicate nested child rows share one conversation projection.
    with patch.object(record, "attention_facts", wraps=record.attention_facts) as attention:
        asyncio.run(
            add_conversation_row_signals(
                conn, board, cast(ConversationSystem, _ConversationFacts()), record
            )
        )
    attention.assert_awaited_once_with({"c_off_today_child"})
    assert {card["id"] for column in board["columns"] for card in column["cards"]} == {
        row.id for row in rows
    }
    [item_row] = board["sprint_items"]
    assert item_row["title"] == "Complete approval queue"
    assert item_row["priority"] == "P0"
    children = {row["id"]: row for row in item_row["tickets"]}
    assert set(children) == {row.id for row in rows}
    owner_approval = children[rows[1].id]
    assert owner_approval["awaiting_approval"] is True
    assert owner_approval["awaiting_reply"] is True
    assert owner_approval["awaiting_agent_approval"] is False
    assert owner_approval["gating_field"] == "brief"
    assert owner_approval["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert owner_approval["worker_type"] == "coding"
    assert owner_approval["day_ids"] == []
    assert owner_approval["blocked"] is False
    assert owner_approval["sprint_item_id"] == item.id
    assert children[rows[0].id]["day_ids"] == ["2026-10-16"]
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
    assert all(not children[row.id]["awaiting_reply"] for row in (rows[0], rows[2], rows[3]))
    conn.close()


@pytest.mark.parametrize("waiting_ask", [False, True])
def test_chief_message_attention_uses_exact_conversation_and_owner_read_mark(
    tmp_path: Path, waiting_ask: bool
) -> None:
    db_path = tmp_path / "chief_messages.db"
    conn = connect(str(db_path))
    create_schema(conn)
    for conversation_id in ("c_chief", "c_child"):
        conn.execute(
            "INSERT INTO conversations "
            "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
            "VALUES (?, 'codex', '/tmp/workspace', 'full', 1, 1)",
            (conversation_id,),
        )
    conn.execute(
        "INSERT INTO agents(agent_key, conversation_id) VALUES ('chief_of_staff', 'c_chief') "
        "ON CONFLICT(agent_key) DO UPDATE SET conversation_id = excluded.conversation_id"
    )
    conn.executemany(
        "INSERT INTO conversation_events "
        "(conversation_id, sequence, kind, payload, created_at) VALUES (?, 1, ?, ?, 1)",
        [
            ("c_chief", "turn_ended", json.dumps({"ending": "completed"})),
            ("c_child", "message_to_owner", "{}"),
        ],
    )
    conn.commit()
    record = ConversationStore(str(db_path))
    system = cast(
        ConversationSystem,
        _ConversationWaitingOnAnAnswer() if waiting_ask else _ConversationFacts(running=True),
    )

    def chief() -> JsonDict:
        return asyncio.run(
            add_agent_conversation_signals(
                [{"employee_id": "chief_of_staff"}], conn, system, record
            )
        )[0]

    quiet = chief()
    assert quiet["conversation_id"] == "c_chief"
    assert quiet["awaiting_reply"] is False
    assert quiet["agent_working"] is True
    assert quiet["needs_me"] is waiting_ask
    assert quiet["latest_turn_ended_sequence"] == 1
    conn.execute(
        "INSERT INTO conversation_events "
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c_chief', 2, 'message_to_owner', '{}', 2)"
    )
    conn.execute("UPDATE conversations SET latest_sequence = 2 WHERE conversation_id = 'c_chief'")
    conn.commit()
    assert chief()["awaiting_reply"] is True
    asyncio.run(record.advance_owner_read_through_sequence("c_chief", 2))
    read = chief()
    assert read["awaiting_reply"] is False
    assert read["owner_read_through_sequence"] == 2
    # The child's unread message is still durable, but never marks the Chief.
    assert asyncio.run(record.attention_facts({"c_child"}))["c_child"].unread_message_to_owner
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
        worker_type="coding",
        title="Khushal writes this Brief",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    seed_historical_worker_type(conn, "personal")
    conn.execute("UPDATE tickets SET worker_type='personal' WHERE id=?", (user_owned.id,))
    conn.commit()
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
    conn.execute("UPDATE tickets SET conversation_id = 'c_answer' WHERE id = ?", (ticket.id,))
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


@pytest.mark.parametrize("unfinished_child", [False, True])
@pytest.mark.parametrize("supervisor_failed", [False, True])
def test_completed_failure_history_does_not_poison_ticket_or_item_attention(
    tmp_path: Path,
    unfinished_child: bool,
    supervisor_failed: bool,
) -> None:
    db_path = tmp_path / "completed.db"
    conn = connect(str(db_path))
    create_schema(conn)
    item = sprints_data.create_item(
        conn, title="Historical failures", project_id="project_vylo", clock=RealClock()
    )
    tickets = [
        tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title=title,
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            sprint_item_id=item.id,
        )
        for title in ("Completed", "Unfinished" if unfinished_child else "Also completed")
    ]
    for index, ticket in enumerate(tickets):
        conversation_id = f"c_history_{index}"
        conn.execute(
            "INSERT INTO conversations "
            "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
            "VALUES (?, 'codex', '/tmp/workspace', 'full', 2, 1)",
            (conversation_id,),
        )
        conn.executemany(
            "INSERT INTO conversation_events "
            "(conversation_id, sequence, kind, payload, created_at) VALUES (?, ?, ?, ?, 2)",
            [
                (conversation_id, 1, "message_to_owner", "{}"),
                (conversation_id, 2, "turn_ended", json.dumps({"ending": "failed"})),
            ],
        )
        stage = "needs_implementation" if index == 1 and unfinished_child else "done"
        conn.execute(
            "UPDATE tickets SET stage = ?, conversation_id = ? WHERE id = ?",
            (stage, conversation_id, ticket.id),
        )
    if supervisor_failed:
        conn.execute(
            "INSERT INTO conversations "
            "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
            "VALUES ('c_supervisor', 'codex', '/tmp/workspace', 'full', 1, 1)"
        )
        conn.execute(
            "INSERT INTO conversation_events "
            "(conversation_id, sequence, kind, payload, created_at) "
            "VALUES ('c_supervisor', 1, 'turn_ended', ?, 2)",
            (json.dumps({"ending": "failed"}),),
        )
        conn.execute(
            "UPDATE agents SET conversation_id = 'c_supervisor' WHERE agent_key = ?",
            (item.supervisor_agent_key,),
        )
    conn.commit()
    rows: list[JsonDict] = [{"id": ticket.id} for ticket in tickets]
    item_row: JsonDict = {"id": item.id}
    record = ConversationStore(str(db_path))
    asyncio.run(
        add_work_attention(
            conn,
            cast(ConversationSystem, _ConversationFacts()),
            record,
            tickets=rows,
            sprint_items=[item_row],
        )
    )
    assert rows[0] == {
        "id": tickets[0].id,
        "awaiting_reply": True,
        "awaiting_answer": False,
        "awaiting_approval": False,
        "awaiting_agent_approval": False,
        "assigned": False,
        "agent_state": "idle",
    }
    assert rows[1]["agent_state"] == ("errored" if unfinished_child else "idle")
    rollup = cast(JsonDict, item_row["ticket_rollup"])
    assert rollup["agent_state"] == ("errored" if unfinished_child else "idle")
    assert rollup["awaiting_reply"] is True
    assert item_row["agent_state"] == ("errored" if supervisor_failed else "idle")
    facts = asyncio.run(record.attention_facts({"c_history_0"}))
    assert facts["c_history_0"].last_turn_failed is True
    assert conn.execute(
        "SELECT payload FROM conversation_events WHERE conversation_id = 'c_history_0' "
        "AND kind = 'turn_ended'"
    ).fetchone()["payload"] == json.dumps({"ending": "failed"})
    conn.close()


@pytest.mark.parametrize(
    ("events", "read_through", "acknowledged_through", "expected"),
    [
        ([], 0, None, (False, False)),
        (["agent_message"], 0, None, (False, False)),
        (["message_to_owner"], 0, None, (True, False)),
        (["message_to_owner"], 1, None, (False, False)),
        (["message_to_owner", "agent_message"], 1, None, (False, False)),
        (["message_to_owner", "agent_message", "message_to_owner"], 1, None, (True, False)),
        (["message_to_owner", "turn_ended"], 1, None, (False, True)),
        (["turn_ended", "agent_message"], 0, None, (False, True)),
        (["turn_ended", "prompt"], 0, None, (False, False)),
        (["turn_ended"], 0, 1, (False, False)),
        (["turn_ended", "message_to_owner"], 0, 1, (True, False)),
        (["turn_ended", "turn_ended"], 0, 1, (False, True)),
    ],
)
def test_attention_facts_preserve_message_and_failure_boundaries(
    tmp_path: Path,
    events: list[str],
    read_through: int,
    acknowledged_through: int | None,
    expected: tuple[bool, bool],
) -> None:
    db_path = tmp_path / "attention.db"
    conn = connect(str(db_path))
    create_schema(conn)
    for conversation_id in ("selected", "unselected"):
        conn.execute(
            "INSERT INTO conversations "
            "(conversation_id, backend_key, workspace_folder, access, latest_sequence, "
            "owner_read_through_sequence, created_at) VALUES (?, 'codex', '/tmp', 'full', ?, ?, 1)",
            (conversation_id, len(events), read_through),
        )
    conn.executemany(
        "INSERT INTO conversation_events "
        "(conversation_id, sequence, kind, payload, created_at) VALUES ('selected', ?, ?, ?, 1)",
        [
            (sequence, kind, json.dumps({"ending": "failed"}) if kind == "turn_ended" else "{}")
            for sequence, kind in enumerate(events, start=1)
        ],
    )
    # An unrequested conversation must not leak its unread message into the selected row.
    conn.execute(
        "INSERT INTO conversation_events "
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('unselected', 10, 'message_to_owner', '{}', 1)"
    )
    if acknowledged_through is not None:
        conn.execute(
            "INSERT INTO conversation_error_acknowledgements "
            "(conversation_id, through_sequence) VALUES ('selected', ?)",
            (acknowledged_through,),
        )
    conn.close()
    record = ConversationStore(str(db_path))
    assert asyncio.run(record.attention_facts(["selected", "selected", "unknown"])) == {
        "selected": ConversationAttentionFacts(*expected)
    }
    assert asyncio.run(record.attention_facts([])) == {}
