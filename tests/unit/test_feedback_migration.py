"""The feedback migration adds durable notes without changing existing Tickets."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema


def test_feedback_migration_preserves_tickets_and_sets_deleted_ticket_to_null(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "feedback-migration.db"
    engine = db_module._migration_engine(str(db_path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), "durable_outcomes")
    engine.dispose()

    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, field_values, "
        "created_at, updated_at) "
        "VALUES ('t_existing', 'Existing', 'coding', 'hermes', 'needs_kickoff', "
        "'needs_success', '{}', 1, 1)"
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)
    upgraded.execute(
        "INSERT INTO feedback_notes "
        "(id, text, state, ticket_id, created_at, updated_at, handled_at) "
        "VALUES ('feedback_existing', 'Stored', 'handled', 't_existing', 2, 3, 3)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "INSERT INTO feedback_notes "
            "(id, text, page_address, page_label, state, created_at, updated_at) "
            "VALUES ('feedback_bad_context', 'Bad', '#/workspace', NULL, 'open', 2, 2)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "INSERT INTO feedback_notes "
            "(id, text, page_address, page_label, state, created_at, updated_at) "
            "VALUES ('feedback_external_context', 'Bad', 'javascript:alert(1)', "
            "'Page', 'open', 2, 2)"
        )
    upgraded.execute("DELETE FROM tickets WHERE id = 't_existing'")

    row = upgraded.execute(
        "SELECT text, state, ticket_id, handled_at FROM feedback_notes "
        "WHERE id = 'feedback_existing'"
    ).fetchone()
    assert tuple(row) == ("Stored", "handled", None, 3)
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    assert (
        upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        == "two_ownership_modes"
    )
    upgraded.close()
