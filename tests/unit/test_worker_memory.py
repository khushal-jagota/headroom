"""The one fact a Worker cannot find out for itself: that its context was compacted."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from planner.core.db import connect, create_schema
from planner.runtime import worker_memory


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(str(tmp_path / "worker-memory.db"))
    create_schema(connection)
    return connection


def _make_conversation(conn: sqlite3.Connection, conversation_id: str, *, compacted: int) -> None:
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, access, "
        "created_at, automatically_compacted_through_sequence) VALUES (?, 'claude', '/tmp', "
        "'full', 0, ?)",
        (conversation_id, compacted),
    )

    if compacted:
        conn.execute(
            "INSERT INTO conversation_events VALUES (?, ?, 'context_compacted', '{}', 0)",
            (conversation_id, compacted + 1),
        )


def _add_prompt(
    conn: sqlite3.Connection,
    conversation_id: str,
    *,
    sequence: int,
    sender_message_id: str | None,
) -> None:
    conn.execute(
        "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, created_at) "
        "VALUES (?, ?, 'prompt', ?, 0)",
        (conversation_id, sequence, json.dumps({"sender_message_id": sender_message_id})),
    )


def _add_ticket(
    conn: sqlite3.Connection, ticket_id: str, *, claim: str, conversation_id: str
) -> None:
    conn.execute(
        "INSERT INTO tickets (id, worker_type, employee_backend, title, stage, "
        "ceiling, field_values, created_at, updated_at, worker_step_claim, "
        "conversation_id) VALUES (?, 'coding', 'claude', ?, "
        "'needs_plan', 'done', '{}', 0, 0, ?, ?)",
        (ticket_id, ticket_id, claim, conversation_id),
    )


def test_a_memory_notice_answers_the_boundary_so_the_same_one_is_never_sent_twice(
    conn: sqlite3.Connection,
) -> None:
    _make_conversation(conn, "c1", compacted=7)
    _add_prompt(conn, "c1", sequence=3, sender_message_id="worker_step_message_abc")
    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c1") is True

    _add_prompt(conn, "c1", sequence=9, sender_message_id="worker_step_memory_notice_xyz")

    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c1") is False
