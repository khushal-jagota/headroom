"""Rehearse the cutover with populated context, agent history and schedule receipts."""

import json
from pathlib import Path

from alembic import command

from planner.core import db


def test_cutover_keeps_planning_context_history_receipts_and_fixed_destinations(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "outcomes.db")
    engine = db._migration_engine(path, 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db._alembic_config(connection), "single_ticket_proposal")
        conn = db.connect(path)
        conn.execute(
            "INSERT INTO sprints(id,name,date_start,date_end,created_at,updated_at) "
            "VALUES ('sp_a','A','2026-07-01','2026-07-07',1,2)"
        )
        conn.execute(
            "INSERT INTO "
            "sprint_items(id,title,body,project_id,sprint_id,created_at,updated_at) "
            "VALUES ('si_planning_a','Edited Planning','shared mutable "
            "context','project_personal','sp_a',3,4)"
        )
        conn.execute(
            "INSERT INTO sprint_items(id,title,body,project_id,created_at,updated_at) "
            "VALUES ('si_loose','Loose','unscheduled context','project_personal',5,6)"
        )
        conn.execute(
            "INSERT INTO "
            "conversations(conversation_id,backend_key,model,workspace_folder,access,"
            "created_at) VALUES ('history','codex','model','/work','full',1)"
        )
        conn.execute(
            "UPDATE agents SET conversation_id='history' WHERE "
            "agent_key='sprint_item_supervisor_si_planning_a'"
        )
        conn.execute(
            "INSERT INTO "
            "tickets(id,title,worker_type,employee_backend,stage,ceiling,field_values,project_id,"
            "sprint_id,sprint_item_id,created_at,updated_at) VALUES ('done','History',"
            "'coding','codex','done','done','{}','project_personal','sp_a','si_planning_a',7,"
            "8)"
        )
        conn.execute(
            "INSERT INTO "
            "tickets(id,title,worker_type,employee_backend,stage,ceiling,field_values,project_id,"
            "sprint_id,sprint_item_id,created_at,updated_at) VALUES ('active','Unfinished',"
            "'coding','codex','needs_plan','done','{}','project_personal','sp_a',"
            "'si_planning_a',9,10)"
        )
        conn.execute(
            "INSERT INTO "
            "scheduled_ticket_schedules(id,enabled,cadence,local_time,title,worker_type,"
            "priority,project_id,sprint_id,sprint_item_id,placement_mode,created_at,"
            "updated_at) VALUES ('fixed',1,'every_planning_day','12:00','Fixed','coding',"
            "'P2','project_personal','sp_a','si_planning_a','sprint_item',11,12)"
        )
        conn.execute(
            "INSERT INTO "
            "scheduled_ticket_schedules(id,enabled,cadence,local_time,title,worker_type,"
            "priority,project_id,sprint_item_id,placement_mode,created_at,"
            "updated_at) VALUES ('loose',0,'every_planning_day','13:00','Loose','coding',"
            "'P3','project_personal','si_loose','sprint_item',13,14)"
        )
        conn.execute(
            "INSERT INTO "
            "scheduled_ticket_occurrences(schedule_id,occurrence_key,target_day_id,outcome,"
            "ticket_id,created_at) VALUES ('fixed','receipt','day_2026-07-04','created',"
            "'done',15)"
        )
        saved_values = json.dumps({"implementation": "  Exact saved context 🌱\n"})
        historical_metadata = "## Historical field metadata\n\n```json\n" + json.dumps(
            {"retired_field": {"unknown_metadata": ["  Exact context 🌱\n", False, None]}}
        ) + "\n```"
        conn.execute(
            "UPDATE tickets SET field_values=?, archived_field_content=? WHERE id='done'",
            (saved_values, historical_metadata),
        )
        artifact = tmp_path / "files" / "sprint-items" / "si_planning_a" / "proof.md"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"# Durable artifact\n\x00")
        items_before = [dict(row) for row in conn.execute("SELECT * FROM sprint_items ORDER BY id")]
        tickets_before = [dict(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")]
        agents_before = [
            dict(row) for row in conn.execute("SELECT * FROM agents ORDER BY agent_key")
        ]
        receipts_before = [
            dict(row) for row in conn.execute("SELECT * FROM scheduled_ticket_occurrences")
        ]
        triggers_before = [
            tuple(row)
            for row in conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND "
                "tbl_name='sprint_items' ORDER BY name"
            )
        ]
        conn.close()
        with engine.begin() as connection:
            command.upgrade(db._alembic_config(connection), "durable_outcomes")
        conn = db.connect(path)
        assert [
            dict(row) for row in conn.execute("SELECT * FROM tickets ORDER BY id")
        ] == tickets_before
        assert [
            dict(row) for row in conn.execute("SELECT * FROM agents ORDER BY agent_key")
        ] == agents_before
        assert [
            dict(row) for row in conn.execute("SELECT * FROM scheduled_ticket_occurrences")
        ] == receipts_before
        assert [dict(row) for row in conn.execute("SELECT * FROM sprint_items ORDER BY id")] == [
            {k: v for k, v in item.items() if k != "sprint_id"} for item in items_before
        ]
        assert [
            tuple(row)
            for row in conn.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND "
                "tbl_name='sprint_items' ORDER BY name"
            )
        ] == triggers_before
        assert [tuple(row) for row in conn.execute("SELECT * FROM sprint_outcomes")] == [
            ("sp_a", "si_planning_a")
        ]
        assert tuple(
            conn.execute(
                "SELECT placement_mode,sprint_id,sprint_item_id FROM "
                "scheduled_ticket_schedules WHERE id='fixed'"
            ).fetchone()
        ) == ("current_sprint", "sp_a", "si_planning_a")
        assert tuple(
            conn.execute(
                "SELECT placement_mode,sprint_id,sprint_item_id FROM "
                "scheduled_ticket_schedules WHERE id='loose'"
            ).fetchone()
        ) == ("backlog", None, "si_loose")
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE "
                "name='idx_sprint_items_one_other_per_sprint_project'"
            ).fetchone()
            is None
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert artifact.read_bytes() == b"# Durable artifact\n\x00"
        conn.close()
    finally:
        engine.dispose()
