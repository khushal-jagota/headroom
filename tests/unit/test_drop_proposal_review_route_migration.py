"""The upgrade strips the stamped reviewer and keeps everything else on the proposal."""

from __future__ import annotations

import json
from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "supervisor_obligations"


def _upgrade_to_previous_revision(path: Path) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    engine.dispose()


def test_upgrade_removes_the_proposal_review_route_and_keeps_the_proposal(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "drop-proposal-review-route.db"
    _upgrade_to_previous_revision(db_path)
    conn = connect(str(db_path))
    fields = {
        "success": {
            "value": None,
            "proposal": {
                "body": "Keep the exact proposal.",
                "proposed_by": "worker-run",
                "created_at": 123,
                "review_route": "agent_review",
            },
            "user_note": "Keep the note.",
        },
        "kickoff": {"value": "Settled kickoff.", "proposal": None, "user_note": None},
    }
    conn.execute(
        "INSERT INTO tickets (id,title,worker_type,employee_backend,stage,priority,"
        "recap,ceiling,at_cap,ticket_status,stage_ownership_overrides,"
        "default_stage_ownership_mode,conversation_id,fields,created_at,updated_at,"
        "ticket_status_changed_at,ticket_status_revision) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "t_stamped_route",
            "Stamped route",
            "coding",
            "codex",
            "needs_success",
            "P2",
            "recap",
            "needs_success",
            "agent_review",
            "awaiting_agent_review",
            "{}",
            "worker",
            "conv_stamped",
            json.dumps(fields),
            10,
            20,
            30,
            7,
        ),
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)
    row = upgraded.execute(
        "SELECT at_cap,ticket_status,fields,ticket_status_changed_at,"
        "ticket_status_revision FROM tickets WHERE id=?",
        ("t_stamped_route",),
    ).fetchone()
    assert row is not None
    assert (row["at_cap"], row["ticket_status"]) == ("agent_review", "awaiting_agent_review")
    migrated_fields = json.loads(str(row["fields"]))
    assert migrated_fields["success"] == {
        "value": None,
        "proposal": {
            "body": "Keep the exact proposal.",
            "proposed_by": "worker-run",
            "created_at": 123,
        },
        "user_note": "Keep the note.",
    }
    assert migrated_fields["kickoff"] == {
        "value": "Settled kickoff.",
        "proposal": None,
        "user_note": None,
    }
    assert (row["ticket_status_changed_at"], row["ticket_status_revision"]) == (30, 7)
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
