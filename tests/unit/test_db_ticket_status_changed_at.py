"""The revision that moves "when did this status change" onto the Ticket row.

A database written before the swap answers that question from its event log. This
revision reads the answer out of the log, writes it onto each Ticket, and drops the log.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

from planner.core.db import MIGRATIONS_DIRECTORY, connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import NO_FURTHER, AtCap

SCHEMA_V37_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "schema_v37.sql"
PREVIOUS_REVISION = "ticket_status_reshape"
# The database is brought all the way up, so it comes to rest at the current head rather
# than at the revision this module is about.
HEAD_REVISION = "one_conversation_system"

_EMPTY_CODING_FIELDS = json.dumps(
    {
        field: {"value": None, "proposal": None, "user_note": None}
        for field in ("kickoff", "success", "approach", "plan", "implementation", "closeout")
    },
    separators=(",", ":"),
)


def _database_at_the_previous_revision(path: Path) -> None:
    """Build the schema the ladder left, then bring it up to just before this revision."""
    conn = connect(str(path))
    conn.executescript(SCHEMA_V37_FIXTURE.read_text(encoding="utf-8"))
    conn.execute("PRAGMA user_version=37")
    conn.close()

    engine = create_engine(
        URL.create(drivername="sqlite", database=str(path)),
        connect_args={"isolation_level": None},
    )
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            config = Config()
            config.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
            config.attributes["connection"] = connection
            command.stamp(config, "baseline_v37")
            command.upgrade(config, PREVIOUS_REVISION)
    finally:
        engine.dispose()


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    ticket_status: str,
    updated_at: int,
) -> None:
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, fields, "
        "alias, ticket_status, stage, created_at, updated_at) VALUES (?, ?, 'coding', 'hermes', "
        "'needs_success', ?, ?, ?, 'needs_success', 1, ?)",
        (
            ticket_id,
            f"Ticket {ticket_id}",
            _EMPTY_CODING_FIELDS,
            f"alias-{ticket_id}",
            ticket_status,
            updated_at,
        ),
    )


def _insert_status_event(
    conn: sqlite3.Connection, ticket_id: str, ticket_status: str, created_at: int
) -> None:
    conn.execute(
        "INSERT INTO events (entity_id, kind, payload, created_at) "
        "VALUES (?, 'ticket_status_changed', ?, ?)",
        (ticket_id, json.dumps({"ticket_status": ticket_status}), created_at),
    )


def _status_changed_at(conn: sqlite3.Connection, ticket_id: str) -> int:
    row = conn.execute(
        "SELECT ticket_status_changed_at FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    assert row is not None
    return int(row["ticket_status_changed_at"])


def _revision(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


@pytest.fixture
def upgraded(tmp_path: Path) -> sqlite3.Connection:
    """A pre-swap database with a history worth reading, brought up to head."""
    db_path = tmp_path / "pre-swap.db"
    _database_at_the_previous_revision(db_path)

    conn = connect(str(db_path))
    # A Ticket that has been through several statuses and now waits on its user. The
    # newest transition is the one that counts, and it is not the newest row overall.
    _insert_ticket(conn, "t_needs_user", ticket_status="needs_user", updated_at=9_000)
    _insert_status_event(conn, "t_needs_user", "agent", 1_000)
    _insert_status_event(conn, "t_needs_user", "empty", 2_000)
    _insert_status_event(conn, "t_needs_user", "needs_user", 3_000)

    # A Ticket that never recorded a status transition falls back to its own updated_at.
    _insert_ticket(conn, "t_never_moved", ticket_status="empty", updated_at=4_242)

    # Another Ticket's history, written after the first one's, must not leak across.
    _insert_ticket(conn, "t_other", ticket_status="agent", updated_at=5_000)
    _insert_status_event(conn, "t_other", "agent", 8_888)

    # A transition recorded in the same second as an earlier one: the later row wins.
    _insert_ticket(conn, "t_same_second", ticket_status="empty", updated_at=6_000)
    _insert_status_event(conn, "t_same_second", "needs_user", 7_777)
    _insert_status_event(conn, "t_same_second", "empty", 7_777)

    conn.execute(
        "INSERT INTO days (id, focus, brief_take, watchout, if_today_lands, notes, "
        "created_at, updated_at) VALUES ('day_2026-07-04', '', '', '', '', '', 1, 1)"
    )
    for position, ticket_id in enumerate(("t_needs_user", "t_never_moved", "t_other")):
        conn.execute(
            "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, ?)",
            ("day_2026-07-04", ticket_id, position),
        )
    conn.close()

    conn = connect(str(db_path))
    create_schema(conn)
    return conn


def test_the_event_log_and_its_index_are_gone(upgraded: sqlite3.Connection) -> None:
    assert _revision(upgraded) == HEAD_REVISION
    names = {
        str(row[0])
        for row in upgraded.execute("SELECT name FROM sqlite_master WHERE sql IS NOT NULL")
    }
    assert "events" not in names
    assert "idx_events_entity" not in names


def test_each_ticket_keeps_the_time_of_its_latest_recorded_status_change(
    upgraded: sqlite3.Connection,
) -> None:
    stored = {
        str(row["id"]): int(row["ticket_status_changed_at"])
        for row in upgraded.execute("SELECT id, ticket_status_changed_at FROM tickets")
    }

    assert stored["t_needs_user"] == 3_000  # the newest of its three transitions
    assert stored["t_other"] == 8_888
    assert stored["t_same_second"] == 7_777
    assert stored["t_never_moved"] == 4_242  # no transitions: its own updated_at
    assert 0 not in stored.values()


def test_review_serves_the_backfilled_time_as_the_wait(
    upgraded: sqlite3.Connection,
) -> None:
    review = tickets_views.review_view(upgraded, day_id="day_2026-07-04")

    assert review["user_help_requests"] == [
        {"ticket_id": "t_needs_user", "title": "Ticket t_needs_user", "waiting_since": 3_000}
    ]


def test_a_new_ticket_and_a_status_change_keep_the_column_current(
    upgraded: sqlite3.Connection,
) -> None:
    created = tickets_data.create_ticket(
        upgraded,
        worker_type="coding",
        title="Fresh",
        actor="human",
        now=10_000,
        title_max_chars=200,
    )
    assert _status_changed_at(upgraded, created.id) == 10_000

    settled = tickets_data.accept_proposal(
        upgraded,
        created.id,
        field="kickoff",
        actor="human",
        now=11_000,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert settled.ticket_status is not created.ticket_status
    assert _status_changed_at(upgraded, created.id) == 11_000

    tickets_data.request_user_help(upgraded, created.id, actor="agent", now=12_000)
    assert _status_changed_at(upgraded, created.id) == 12_000

    # Asking again changes nothing, so the time stays where it was.
    tickets_data.request_user_help(upgraded, created.id, actor="agent", now=13_000)
    assert _status_changed_at(upgraded, created.id) == 12_000
