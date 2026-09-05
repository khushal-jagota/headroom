"""Focused coverage for the Planning Day workflow amendment."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "conversation_automatic_compaction"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def _old_fields(
    *,
    kickoff: str | None = None,
    closeout: str | None = None,
    planning_note: str | None = None,
) -> str:
    return json.dumps(
        {
            field: {
                "value": value,
                "proposal": None,
                "user_note": planning_note if field == "planning" else None,
            }
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
        fields=_old_fields(
            kickoff="Old review",
            closeout="Old closeout",
            planning_note="  Old note\n",
        ),
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    stranded = upgraded.execute(
        "SELECT stage, ceiling, ticket_status, default_stage_ownership_mode, fields, guidance "
        "FROM tickets WHERE id = 't_stranded'"
    ).fetchone()
    assert tuple(stranded)[:4] == ("needs_review", "needs_review", "empty", "worker")
    stranded_fields = json.loads(stranded["fields"])
    assert stranded_fields == {
        field: {"value": None, "proposal": None}
        for field in (
            "kickoff",
            "gather",
            "planning",
            "closeout",
            "review",
            "direction",
            "day_changes",
        )
    }
    assert stranded["guidance"] == ""

    done = upgraded.execute(
        "SELECT stage, ceiling, ticket_status, fields, guidance FROM tickets WHERE id = 't_done'"
    ).fetchone()
    assert tuple(done)[:3] == ("done", "done", "empty")
    done_fields = json.loads(done["fields"])
    assert done_fields == {
        "kickoff": {"value": "Old review", "proposal": None},
        "gather": {"value": None, "proposal": None},
        "planning": {"value": None, "proposal": None},
        "closeout": {"value": "Old closeout", "proposal": None},
        "review": {"value": None, "proposal": None},
        "direction": {"value": None, "proposal": None},
        "day_changes": {"value": None, "proposal": None},
    }
    assert done["guidance"] == "## planning\n\n  Old note\n"
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
