"""Upgrade preserves old undelivered guidance even after its stage no longer matches.

Prior feedback tests prove replacement only. This upgrade boundary proves that no
retained legacy text disappears and no exact proposal identity gets invented.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from planner.core.db import (
    connect,
    create_schema,
    schema_migration_transaction,
    upgrade_schema_connection,
)
from planner.worker_types.shipped import shipped_worker_types
from planner.worker_types.store import definition_from_json, workflow_from_preset, workflow_to_json


def test_legacy_feedback_moves_to_durable_delivery_without_invented_proposal(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "legacy.db")
    with schema_migration_transaction(path) as migration:
        upgrade_schema_connection(migration, "memory_notice_turns_are_maintenance")
    note = "  complete legacy\nUnicode café\nlast  "
    coding = next(value for value in shipped_worker_types() if value["worker_type"] == "coding")
    workflow = workflow_to_json(workflow_from_preset(definition_from_json(json.dumps(coding))))
    ticket_id = "t_legacy"
    # Current Ticket writers require named machines, which this historical schema lacks.
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,workflow_json,created_at,updated_at) "
            "VALUES(?,'Legacy','coding','codex','needs_brief','needs_brief','{}',?,1,1)",
            (ticket_id, workflow),
        )
        conn.execute(
            "INSERT INTO ticket_revision_feedback "
            "(ticket_id,stage,feedback_json,revision,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (
                ticket_id,
                "a_previous_stage",
                json.dumps([{"sender_kind": "owner", "sender_id": "owner", "message": note}]),
                2,
                3,
                3,
            ),
        )
        conn.commit()
    with closing(connect(path)) as conn:
        create_schema(conn)
        row = conn.execute(
            "SELECT * FROM ticket_decision_messages WHERE ticket_id=?", (ticket_id,)
        ).fetchone()
        assert row["message"] == note
        assert row["proposal_revision"] is None
        assert row["delivery_status"] == "pending"
        assert (
            row["model_text"]
            == f"Legacy revision feedback from User for stage a_previous_stage:\n{note}"
        )
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='ticket_revision_feedback'"
            ).fetchone()
            is None
        )
