"""A real schema upgrade must preserve Ticket state and all attached schema objects."""

from __future__ import annotations

import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from tests.unit.test_db import _migration_tree_through

from planner.core import db
from planner.database_modules import attach
from planner.worker_types.store import workflow_from_row


def test_upgrade_copies_workflows_and_preserves_ticket_state_and_triggers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "goal_outcomes")
    with closing(db.connect(str(tmp_path / "workflow-upgrade.db"))) as conn:
        db.create_schema(conn)
        conn.execute(
            "INSERT INTO tickets (id,title,worker_type,employee_backend,employee_launch_model,"
            "stage,ceiling,field_values,field_approvals,pending_proposal,pending_proposal_revision,"
            "worker_step_claim,worker_step_claim_revision,created_at,updated_at) "
            "VALUES ('t_saved','Saved Ticket','coding','codex','owner-model',"
            "'needs_what_changes','needs_what_changes',?,?,?,7,'out',9,10,11)",
            (
                json.dumps({"brief": "Keep this", "historical_extra": "Hidden text"}),
                json.dumps({"brief": {"method": "owner", "actor": "owner", "label": "Owner"}}),
                json.dumps(
                    {
                        "field": "what_changes",
                        "body": "Pending",
                        "proposed_by": "worker",
                        "created_at": 10,
                    }
                ),
            ),
        )
        conn.execute(
            "INSERT INTO tickets (id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at) "
            "VALUES ('t_done','History','coding','codex','done','historical_ceiling','{}',1,2)"
        )
        conn.execute(
            "INSERT INTO tickets (id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at) "
            "VALUES ('t_orphan','Missing preset','coding','codex',"
            "'needs_brief','needs_brief','{}',1,2)"
        )
        conn.execute("CREATE TABLE workflow_migration_audit(ticket_id TEXT)")
        conn.execute(
            "CREATE TRIGGER workflow_migration_probe AFTER UPDATE OF title ON tickets "
            "BEGIN INSERT INTO workflow_migration_audit VALUES (NEW.id); END"
        )
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(tickets)")]
        select = ",".join(columns)
        before = [
            tuple(row)
            for row in conn.execute(
                f"SELECT {select} FROM tickets WHERE id != 't_orphan' ORDER BY id"
            )
        ]
        sources = [
            tuple(row) for row in conn.execute("SELECT * FROM managed_skills ORDER BY skill_name")
        ]
        triggers = [
            tuple(row)
            for row in conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='tickets' ORDER BY name"
            )
        ]
        indexes = [tuple(row) for row in conn.execute("PRAGMA index_list(tickets)")]
        conn.commit()
        with closing(sqlite3.connect(str(tmp_path / "workflow-upgrade.db"))) as raw:
            attach(raw)
            raw.execute("BEGIN")
            raw.execute("UPDATE tickets SET worker_type='missing_type' WHERE id='t_orphan'")
            raw.commit()
        shutil.copy(
            shipped / "versions/ticket_owned_workflows.py",
            tree / "versions/ticket_owned_workflows.py",
        )
        db.create_schema(conn)
        assert [
            tuple(row)
            for row in conn.execute(
                f"SELECT {select} FROM tickets WHERE id != 't_orphan' ORDER BY id"
            )
        ] == before
        assert [
            tuple(row) for row in conn.execute("SELECT * FROM managed_skills ORDER BY skill_name")
        ] == sources
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
                "AND tbl_name='tickets' ORDER BY name"
            )
        ] == triggers
        assert {row[1] for row in conn.execute("PRAGMA index_list(tickets)")} == {
            row[1] for row in indexes
        }
        workflow = workflow_from_row(
            conn.execute("SELECT * FROM tickets WHERE id='t_saved'").fetchone()
        )
        assert workflow.specialist_skill == "panels-worker-coding"
        assert workflow.gating_field("needs_what_changes") == "what_changes"
        assert (
            conn.execute(
                "SELECT integrity_error_reason FROM tickets WHERE id='t_orphan'"
            ).fetchone()[0]
            == "stored Worker definition missing"
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.execute("UPDATE tickets SET title='Updated' WHERE id='t_saved'")
        assert (
            conn.execute("SELECT ticket_id FROM workflow_migration_audit").fetchone()[0]
            == "t_saved"
        )
