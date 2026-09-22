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


TODAY = "day_2026-09-21"


def _place_on_the_day(conn: sqlite3.Connection, ticket_id: str) -> None:
    conn.execute(
        "INSERT INTO days (id, created_at, updated_at) VALUES (?, 0, 0) "
        "ON CONFLICT(id) DO NOTHING",
        (TODAY,),
    )
    conn.execute(
        "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, 0)",
        (TODAY, ticket_id),
    )


def _add_ticket(
    conn: sqlite3.Connection, ticket_id: str, *, claim: str, conversation_id: str
) -> None:
    conn.execute(
        "INSERT INTO tickets (id, worker_type, employee_backend, title, stage, priority, "
        "ceiling, field_values, created_at, updated_at, worker_step_claim, "
        "conversation_id) VALUES (?, 'coding', 'claude', ?, "
        "'needs_plan', 'P2', 'done', '{}', 0, 0, ?, ?)",
        (ticket_id, ticket_id, claim, conversation_id),
    )


def test_a_conversation_compacted_after_the_last_worker_step_message_holds_a_loss(
    conn: sqlite3.Connection,
) -> None:
    _make_conversation(conn, "c1", compacted=7)
    _add_prompt(conn, "c1", sequence=3, sender_message_id="worker_step_message_abc")

    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c1") is True


def test_a_boundary_before_the_last_worker_step_message_is_already_answered(
    conn: sqlite3.Connection,
) -> None:
    _make_conversation(conn, "c1", compacted=3)
    _add_prompt(conn, "c1", sequence=3, sender_message_id="worker_step_message_abc")
    _add_prompt(conn, "c1", sequence=9, sender_message_id="worker_step_message_def")

    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c1") is False


def test_a_memory_notice_answers_the_boundary_so_the_same_one_is_never_sent_twice(
    conn: sqlite3.Connection,
) -> None:
    _make_conversation(conn, "c1", compacted=7)
    _add_prompt(conn, "c1", sequence=3, sender_message_id="worker_step_message_abc")
    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c1") is True

    _add_prompt(conn, "c1", sequence=8, sender_message_id="worker_step_memory_notice_xyz")

    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c1") is False


def test_a_conversation_panels_never_woke_has_no_memory_to_have_lost(
    conn: sqlite3.Connection,
) -> None:
    _make_conversation(conn, "c1", compacted=7)
    _add_prompt(conn, "c1", sequence=3, sender_message_id="owner_message_abc")
    _add_prompt(conn, "c1", sequence=4, sender_message_id=None)

    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "c1") is False


def test_a_ticket_with_no_conversation_holds_no_loss(conn: sqlite3.Connection) -> None:
    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, None) is False


def test_an_unknown_conversation_holds_no_loss(conn: sqlite3.Connection) -> None:
    assert worker_memory.conversation_holds_an_unanswered_memory_loss(conn, "missing") is False


def test_only_a_ticket_part_way_through_a_step_is_found_by_the_scan(
    conn: sqlite3.Connection,
) -> None:
    _make_conversation(conn, "mid-step", compacted=7)
    _make_conversation(conn, "resting", compacted=7)
    for conversation_id in ("mid-step", "resting"):
        _add_prompt(conn, conversation_id, sequence=3, sender_message_id="worker_step_message_a")
    _add_ticket(conn, "t_mid", claim="out", conversation_id="mid-step")
    _add_ticket(conn, "t_rest", claim="none", conversation_id="resting")
    _place_on_the_day(conn, "t_mid")
    _place_on_the_day(conn, "t_rest")

    assert worker_memory.ticket_ids_holding_an_unanswered_memory_loss(
        conn, planning_day_id=TODAY
    ) == ("t_mid",)


def test_a_ticket_that_is_not_on_the_planning_day_is_left_asleep(
    conn: sqlite3.Connection,
) -> None:
    _make_conversation(conn, "off-day", compacted=7)
    _add_prompt(conn, "off-day", sequence=3, sender_message_id="worker_step_message_a")
    _add_ticket(conn, "t_off", claim="out", conversation_id="off-day")

    assert (
        worker_memory.ticket_ids_holding_an_unanswered_memory_loss(conn, planning_day_id=TODAY)
        == ()
    )
    assert (
        worker_memory.ticket_is_on_the_planning_day(conn, "t_off", planning_day_id=TODAY)
        is False
    )


def test_every_message_id_the_rule_counts_on_starts_with_the_same_prefix() -> None:
    assert worker_memory.new_worker_step_message_id().startswith(
        worker_memory.ANSWERING_SENDER_MESSAGE_PREFIX
    )
    assert worker_memory.new_memory_notice_message_id().startswith(
        worker_memory.ANSWERING_SENDER_MESSAGE_PREFIX
    )
