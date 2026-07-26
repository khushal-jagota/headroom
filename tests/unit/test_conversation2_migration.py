"""The revision that gives the conversation system its two tables.

A database that has never seen it is brought up from empty, which is how a fresh Panels
install gets them and how every other database gets them the next time it is opened.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from planner.core.db import connect, create_schema

HEAD_REVISION = "agents"


def _table_columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, int, int]]:
    """Each column's name, type, NOT NULL and place in the primary key."""
    return [
        (str(row[1]), str(row[2]), int(row[3]), int(row[5]))
        for row in conn.execute(f"PRAGMA table_info({table})")
    ]


@pytest.fixture
def upgraded(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(str(tmp_path / "conversations.db"))
    create_schema(conn)
    return conn


def test_the_upgrade_leaves_the_database_at_this_revision(upgraded: sqlite3.Connection) -> None:
    assert (
        str(upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0])
        == HEAD_REVISION
    )


def test_a_conversation_holds_what_it_was_started_with_and_where_it_has_got_to(
    upgraded: sqlite3.Connection,
) -> None:
    assert _table_columns(upgraded, "conversations") == [
        ("conversation_id", "TEXT", 0, 1),
        ("backend_key", "TEXT", 1, 0),
        ("model", "TEXT", 0, 0),
        ("reasoning_effort", "TEXT", 0, 0),
        ("workspace_folder", "TEXT", 1, 0),
        ("role_text", "TEXT", 0, 0),
        ("identity_environment_variables", "TEXT", 1, 0),
        ("access", "TEXT", 1, 0),
        ("vendor_session_cursor", "TEXT", 0, 0),
        ("latest_sequence", "INTEGER", 1, 0),
        ("created_at", "INTEGER", 1, 0),
    ]


def test_a_row_of_a_conversations_record_is_keyed_by_its_place_in_that_conversation(
    upgraded: sqlite3.Connection,
) -> None:
    assert _table_columns(upgraded, "conversation_events") == [
        ("conversation_id", "TEXT", 1, 1),
        ("sequence", "INTEGER", 1, 2),
        ("kind", "TEXT", 1, 0),
        ("payload", "TEXT", 1, 0),
        ("created_at", "INTEGER", 1, 0),
    ]

    upgraded.execute(
        "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, access, "
        "created_at) VALUES ('c', 'hermes', '/tmp/workspace', 'full', 1)"
    )
    upgraded.execute(
        "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('c', 1, 'prompt', '{}', 1)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
            "created_at) VALUES ('c', 1, 'agent_message', '{}', 1)"
        )


def test_a_conversation_starts_with_no_environment_and_no_record(
    upgraded: sqlite3.Connection,
) -> None:
    """The two defaults the DDL carries, read back off a row that named neither."""
    upgraded.execute(
        "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, access, "
        "created_at) VALUES ('c', 'codex', '/tmp/workspace', 'full', 1)"
    )
    row = upgraded.execute(
        "SELECT identity_environment_variables, latest_sequence FROM conversations"
    ).fetchone()

    assert str(row["identity_environment_variables"]) == "[]"
    assert int(row["latest_sequence"]) == 0


def test_a_row_cannot_belong_to_a_conversation_that_is_not_there(
    upgraded: sqlite3.Connection,
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
            "created_at) VALUES ('never-started', 1, 'prompt', '{}', 1)"
        )


def test_this_revision_has_no_way_back(upgraded: sqlite3.Connection) -> None:
    """Dropping the tables would throw away every conversation, so it refuses to."""
    from planner.core.migrations.versions import conversation_system_tables

    with pytest.raises(NotImplementedError):
        conversation_system_tables.downgrade()
