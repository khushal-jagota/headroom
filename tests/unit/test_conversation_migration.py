"""The revisions the conversation system's tables are made of.

One gives it its two tables, one adds the commands a conversation's agent offers, and one
lets go of conversations nothing was ever said in. A database that has never seen them is
brought up from empty, which is how a fresh Panels install gets them and how every other
database gets them the next time it is opened.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from planner.conversation.storage import ConversationStore
from planner.core.db import connect, create_schema

HEAD_REVISION = "scheduled_ticket_creation"


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
        ("available_commands", "TEXT", 1, 0),
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
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, created_at) VALUES ('c', 'hermes', 'a-model', '/tmp/workspace', 'full', 1)"
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


def test_neither_revision_has_a_way_back(upgraded: sqlite3.Connection) -> None:
    """Both would throw away rows nothing could put back, so both refuse to.

    Dropping the conversation tables would throw away every conversation. Undoing the
    other would have to refill five tables whose writers no longer exist.
    """
    del upgraded
    from planner.core.migrations.versions import (
        conversation_system_tables,
        one_conversation_system,
    )

    with pytest.raises(NotImplementedError):
        conversation_system_tables.downgrade()
    with pytest.raises(NotImplementedError):
        one_conversation_system.downgrade()


# --- what the layer that came before left behind ----------------------------------------


def _build_a_database_at(path: Path, revision: str) -> sqlite3.Connection:
    """A database built the ordinary way and then stopped at a revision short of head.

    The shape it has is the shape a real database on that release has, which is what a
    revision has to be able to arrive on top of.
    """
    from alembic import command

    from planner.core import db as db_module

    engine = db_module._migration_engine(str(path), 5000)  # noqa: SLF001
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), revision)  # noqa: SLF001
    finally:
        engine.dispose()
    return connect(str(path))


def test_the_retired_tables_go_and_the_ticket_keeps_its_link_under_its_real_name(
    tmp_path: Path,
) -> None:
    path = tmp_path / "previous-head.db"
    conn = _build_a_database_at(path, "agents")
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, fields, "
        "employee_session_id, created_at, updated_at) VALUES ('t_linked', 'Linked', 'coding', "
        "'codex', 'needs_kickoff', '{}', 'conversation-abc', 1, 1)"
    )
    conn.execute(
        "INSERT INTO employee_step_runs (employee_step_id, ticket_id, status, started_at, "
        "updated_at) VALUES ('step', 't_linked', 'complete', 1, 1)"
    )
    conn.execute(
        "INSERT INTO ticket_conversation_projections (ticket_id, updated_at) VALUES ('t_linked', 1)"
    )
    # A conversation somebody actually spoke in, because that is what a linked Ticket has:
    # a later revision lets go of links to conversations nothing was ever said in.
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, access, "
        "created_at) VALUES ('conversation-abc', 'codex', '/tmp/workspace', 'full', 1)"
    )
    conn.execute(
        "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, created_at) "
        "VALUES ('conversation-abc', 1, 'prompt', '{}', 1)"
    )
    conn.commit()

    create_schema(conn)

    tables = {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "conversation_session_bindings",
        "employee_conversations",
        "employee_configuration_catalog_cache",
        "employee_step_runs",
        "ticket_conversation_projections",
    }.isdisjoint(tables)
    # The index went with the table it was on rather than being left dangling.
    assert not [
        row
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = ?",
            ("idx_employee_step_runs_one_running",),
        )
    ]
    # The link itself is untouched. Only what it is called changed, because what it holds
    # had already changed.
    assert (
        conn.execute("SELECT conversation_id FROM tickets WHERE id = 't_linked'").fetchone()[0]
        == "conversation-abc"
    )
    assert "employee_session_id" not in {
        str(row[1]) for row in conn.execute("PRAGMA table_info(tickets)")
    }
    conn.close()


# --- the commands a conversation's agent offers ------------------------------------------


def test_a_conversation_from_before_the_column_arrives_with_no_commands(
    tmp_path: Path,
) -> None:
    """Every conversation already in a real database comes through this revision.

    None of them has ever had a menu reported, and an empty one is the true answer for
    them — the same answer a conversation started after the revision begins with.
    """
    path = tmp_path / "before-the-column.db"
    conn = _build_a_database_at(path, "one_conversation_system")
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, created_at) VALUES ('c', 'hermes', 'a-model', '/tmp/workspace', 'full', 1)"
    )
    conn.commit()

    create_schema(conn)

    assert (
        str(
            conn.execute(
                "SELECT available_commands FROM conversations WHERE conversation_id = 'c'"
            ).fetchone()[0]
        )
        == "[]"
    )
    conn.close()

    read = asyncio.run(ConversationStore(str(path)).read_conversation("c"))
    assert read is not None
    assert read.available_commands == ()


# --- conversations nothing was ever said in ----------------------------------------------


def _conversation(conn: sqlite3.Connection, conversation_id: str, *, spoken_in: bool) -> None:
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, workspace_folder, access, "
        "created_at, vendor_session_cursor) VALUES (?, 'claude', '/tmp/workspace', 'full', 1, ?)",
        (conversation_id, f"session-for-{conversation_id}"),
    )
    if spoken_in:
        conn.execute(
            "INSERT INTO conversation_events (conversation_id, sequence, kind, payload, "
            "created_at) VALUES (?, 1, 'prompt', '{}', 1)",
            (conversation_id,),
        )


def test_owners_let_go_of_conversations_nothing_was_ever_said_in(tmp_path: Path) -> None:
    """The stuck ones are cut loose on the upgrade, and the working ones are untouched.

    A conversation used to be made before there was anything to say, and the message that
    followed could not reach it. Under the rule this revision serves, one nothing was said
    in does not exist — so a Ticket or an agent pointing at one is pointing at nothing, and
    is left where New leaves it.
    """
    path = tmp_path / "before-letting-go.db"
    conn = _build_a_database_at(path, "conversation_available_commands")
    _conversation(conn, "never-spoke", spoken_in=False)
    _conversation(conn, "spoke", spoken_in=True)
    _conversation(conn, "chief-never-spoke", spoken_in=False)
    for ticket_id, conversation_id in (("t_stuck", "never-spoke"), ("t_working", "spoke")):
        conn.execute(
            "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, fields, "
            "conversation_id, created_at, updated_at) VALUES (?, 'T', 'coding', 'claude', "
            "'needs_kickoff', '{}', ?, 1, 1)",
            (ticket_id, conversation_id),
        )
    conn.execute(
        "INSERT INTO agents (agent_key, conversation_id) VALUES ('chief', 'chief-never-spoke')"
    )
    conn.commit()

    create_schema(conn)

    assert dict(conn.execute("SELECT id, conversation_id FROM tickets")) == {
        "t_stuck": None,
        "t_working": "spoke",
    }
    assert dict(conn.execute("SELECT agent_key, conversation_id FROM agents")) == {"chief": None}
    conn.close()
