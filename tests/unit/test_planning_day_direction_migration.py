"""Focused coverage for the Planning Day workflow amendment."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "conversation_automatic_compaction"
HEAD_REVISION = "planning_day_direction"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def _old_fields(*, kickoff: str | None = None, closeout: str | None = None) -> str:
    return json.dumps(
        {
            field: {"value": value, "proposal": None, "user_note": None}
            for field, value in (
                ("kickoff", kickoff),
                ("gather", None),
                ("planning", None),
                ("closeout", closeout),
            )
        },
        separators=(",", ":"),
    )


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    stage: str,
    ceiling: str,
    ticket_status: str,
    fields: str,
) -> None:
    conn.execute(
        "INSERT INTO tickets "
        "(id, title, worker_type, employee_backend, stage, ceiling, ticket_status, fields, "
        "created_at, updated_at) VALUES (?, ?, 'planning-day', 'claude', ?, ?, ?, ?, 1, 1)",
        (ticket_id, ticket_id, stage, ceiling, ticket_status, fields),
    )


def test_upgrade_reconciles_removed_stages_and_preserves_old_content(tmp_path: Path) -> None:
    db_path = tmp_path / "planning-day-direction.db"
    conn = _upgrade_to_previous_revision(db_path)
    _insert_ticket(
        conn,
        "t_stranded",
        stage="needs_kickoff",
        ceiling="needs_kickoff",
        ticket_status="awaiting_approval",
        fields=_old_fields(),
    )
    _insert_ticket(
        conn,
        "t_done",
        stage="done",
        ceiling="done",
        ticket_status="empty",
        fields=_old_fields(kickoff="Old review", closeout="Old closeout"),
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
        HEAD_REVISION
    )
    stranded = upgraded.execute(
        "SELECT stage, ceiling, ticket_status, default_stage_ownership_mode, fields "
        "FROM tickets WHERE id = 't_stranded'"
    ).fetchone()
    assert tuple(stranded)[:4] == ("needs_review", "needs_review", "empty", "worker")
    stranded_fields = json.loads(stranded["fields"])
    assert all(
        stranded_fields[field] == {"value": None, "proposal": None, "user_note": None}
        for field in ("review", "direction", "day_changes", "closeout")
    )

    done = upgraded.execute(
        "SELECT stage, ceiling, ticket_status, fields FROM tickets WHERE id = 't_done'"
    ).fetchone()
    assert tuple(done)[:3] == ("done", "done", "empty")
    done_fields = json.loads(done["fields"])
    assert done_fields["kickoff"]["value"] == "Old review"
    assert done_fields["closeout"]["value"] == "Old closeout"
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
