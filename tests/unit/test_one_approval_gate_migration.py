"""The one-approval-gate migration collapses both review routes without losing state."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from sqlite3 import Connection

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect

PREVIOUS_REVISION = "drop_supervisor_obligations"

_INSERT_TICKET = (
    "INSERT INTO tickets (id,title,worker_type,employee_backend,stage,priority,"
    "recap,ceiling,at_cap,ticket_status,stage_ownership_overrides,"
    "default_stage_ownership_mode,conversation_id,fields,created_at,updated_at,"
    "ticket_status_changed_at,ticket_status_revision) VALUES "
    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _upgrade_to_previous_revision(path: Path) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    engine.dispose()


def _fields_with_a_parked_proposal() -> str:
    return json.dumps(
        {
            "success": {
                "value": None,
                "proposal": {
                    "body": "Keep the exact proposal.",
                    "proposed_by": "sprint_item_supervisor",
                    "created_at": 123,
                },
                "user_note": "Keep the note.",
            },
            "approach": {"value": None, "proposal": None, "user_note": None},
        }
    )


def _seed(conn: sqlite3.Connection, ticket_id: str, at_cap: str, ticket_status: str) -> None:
    conn.execute(
        _INSERT_TICKET,
        (
            ticket_id,
            "Legacy route",
            "coding",
            "codex",
            "needs_success",
            "P2",
            "recap",
            "needs_success",
            at_cap,
            ticket_status,
            '{"needs_success":"paired"}',
            "worker",
            f"conv_{ticket_id}",
            _fields_with_a_parked_proposal(),
            10,
            20,
            30,
            7,
        ),
    )


def test_upgrade_collapses_both_routes_onto_one_approval_gate(tmp_path: Path) -> None:
    db_path = tmp_path / "one-gate.db"
    _upgrade_to_previous_revision(db_path)
    conn = connect(str(db_path))
    _seed(conn, "t_agent_routed", "agent_review", "awaiting_agent_review")
    _seed(conn, "t_user_routed", "user_review", "awaiting_user_review")
    _seed(conn, "t_stopped", "stop", "empty")
    conn.close()

    upgraded = connect(str(db_path))
    _upgrade_legacy(upgraded)

    assert {
        str(row["id"]): (str(row["at_cap"]), str(row["ticket_status"]))
        for row in upgraded.execute("SELECT id, at_cap, ticket_status FROM tickets")
    } == {
        "t_agent_routed": ("propose", "awaiting_approval"),
        "t_user_routed": ("propose", "awaiting_approval"),
        "t_stopped": ("stop", "empty"),
    }
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()


def test_upgrade_preserves_the_parked_proposal_and_ticket_control_metadata(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "one-gate-preserved.db"
    _upgrade_to_previous_revision(db_path)
    conn = connect(str(db_path))
    _seed(conn, "t_agent_routed", "agent_review", "awaiting_agent_review")
    conn.close()

    upgraded = connect(str(db_path))
    _upgrade_legacy(upgraded)
    row = upgraded.execute(
        "SELECT stage,ceiling,stage_ownership_overrides,default_stage_ownership_mode,"
        "conversation_id,fields,guidance,ticket_status_changed_at,ticket_status_revision "
        "FROM tickets WHERE id=?",
        ("t_agent_routed",),
    ).fetchone()
    assert row is not None
    assert tuple(row)[:5] == (
        "needs_success",
        "needs_success",
        '{"needs_success":"paired"}',
        "worker",
        "conv_t_agent_routed",
    )
    # A rename of two values must not disturb when a Ticket last moved, or which
    # transition it is on: freshness and identity are what readers watch.
    assert (row["ticket_status_changed_at"], row["ticket_status_revision"]) == (30, 7)
    migrated_fields = json.loads(str(row["fields"]))
    assert migrated_fields["success"] == {
        "value": None,
        "proposal": {
            "body": "Keep the exact proposal.",
            "proposed_by": "sprint_item_supervisor",
            "created_at": 123,
        },
    }
    assert migrated_fields["approach"] == {"value": None, "proposal": None}
    assert row["guidance"] == "## success\n\nKeep the note."
    upgraded.close()


def test_the_retired_vocabulary_is_refused_after_the_upgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "one-gate-check.db"
    _upgrade_to_previous_revision(db_path)
    conn = connect(str(db_path))
    _seed(conn, "t_user_routed", "user_review", "awaiting_user_review")
    conn.close()

    upgraded = connect(str(db_path))
    _upgrade_legacy(upgraded)
    for column, retired in (
        ("at_cap", "agent_review"),
        ("at_cap", "user_review"),
        ("ticket_status", "awaiting_agent_review"),
        ("ticket_status", "awaiting_user_review"),
    ):
        with pytest.raises(sqlite3.IntegrityError):
            upgraded.execute(
                f"UPDATE tickets SET {column} = ? WHERE id = 't_user_routed'", (retired,)
            )
    upgraded.close()


def _upgrade_legacy(conn: Connection) -> None:
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), "ticket_guidance")
    finally:
        engine.dispose()
