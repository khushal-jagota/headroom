"""The review-route migration preserves parked proposals and Ticket control state."""

from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect

PREVIOUS_REVISION = "sprint_item_supervisors"


def _upgrade_to_previous_revision(path: Path) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    engine.dispose()


def test_upgrade_maps_legacy_review_state_without_losing_proposal_or_control_metadata(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "review-routes.db"
    _upgrade_to_previous_revision(db_path)
    conn = connect(str(db_path))
    fields = {
        "success": {
            "value": None,
            "proposal": {
                "body": "Keep the exact proposal.",
                "proposed_by": "worker-run",
                "created_at": 123,
            },
            "user_note": "Keep the note.",
        },
        "approach": {"value": None, "proposal": None, "user_note": None},
    }
    conn.execute(
        "INSERT INTO tickets (id,title,worker_type,employee_backend,stage,priority,"
        "recap,ceiling,at_cap,ticket_status,stage_ownership_overrides,"
        "default_stage_ownership_mode,conversation_id,fields,created_at,updated_at,"
        "ticket_status_changed_at,ticket_status_revision) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "t_legacy_review",
            "Legacy review",
            "coding",
            "codex",
            "needs_success",
            "P2",
            "recap",
            "needs_success",
            "propose",
            "awaiting_approval",
            '{"needs_success":"paired"}',
            "worker",
            "conv_legacy",
            json.dumps(fields),
            10,
            20,
            30,
            7,
        ),
    )
    conn.close()

    upgraded = connect(str(db_path))
    _upgrade_legacy(upgraded)
    row = upgraded.execute(
        "SELECT stage,ceiling,at_cap,ticket_status,stage_ownership_overrides,"
        "default_stage_ownership_mode,conversation_id,fields,guidance,"
        "ticket_status_changed_at,ticket_status_revision FROM tickets WHERE id=?",
        ("t_legacy_review",),
    ).fetchone()
    assert row is not None
    assert tuple(row)[:7] == (
        "needs_success",
        "needs_success",
        "propose",
        "awaiting_approval",
        '{"needs_success":"paired"}',
        "worker",
        "conv_legacy",
    )
    migrated_fields = json.loads(str(row["fields"]))
    assert migrated_fields["success"] == {
        "value": None,
        "proposal": {
            "body": "Keep the exact proposal.",
            "proposed_by": "worker-run",
            "created_at": 123,
        },
    }
    assert migrated_fields["approach"] == {"value": None, "proposal": None}
    assert row["guidance"] == "## success\n\nKeep the note."
    assert (row["ticket_status_changed_at"], row["ticket_status_revision"]) == (30, 7)
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()


def _upgrade_legacy(conn: Connection) -> None:
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), "ticket_guidance")
    finally:
        engine.dispose()
