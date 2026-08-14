"""Focused migration coverage for Ticket trouble notes."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "ticket_judgments"
HEAD_REVISION = "one_approval_gate"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def test_upgrade_adds_ordered_notes_under_existing_judgments(tmp_path: Path) -> None:
    db_path = tmp_path / "trouble-notes-migration.db"
    conn = _upgrade_to_previous_revision(db_path)
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES ('project_test', 'Test', '', 1, 1)"
    )
    conn.execute(
        "INSERT INTO tickets "
        "(id, worker_type, employee_backend, stage, title, project_id, ceiling, fields, "
        "created_at, updated_at) VALUES "
        "('t_existing', 'coding', 'codex', 'done', 'Existing', 'project_test', "
        "'done', '{}', 1, 1)"
    )
    conn.execute(
        "INSERT INTO ticket_judgments (ticket_id, verdict_rating, verdict_text) "
        "VALUES ('t_existing', 4, 'Keep this')"
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
        HEAD_REVISION
    )
    assert tuple(
        upgraded.execute(
            "SELECT verdict_rating, verdict_text FROM ticket_judgments "
            "WHERE ticket_id = 't_existing'"
        ).fetchone()
    ) == (4, "Keep this")
    foreign_keys = upgraded.execute(
        "PRAGMA foreign_key_list(ticket_judgment_trouble_notes)"
    ).fetchall()
    assert [(row["table"], row["from"], row["to"], row["on_delete"]) for row in foreign_keys] == [
        ("ticket_judgments", "ticket_id", "ticket_id", "CASCADE")
    ]
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
