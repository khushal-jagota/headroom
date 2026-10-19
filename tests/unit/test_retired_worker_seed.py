"""Catalog retirement changes supply, never historical work or owner skill text."""

import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import op

from planner.core.db import connect, create_schema
from planner.core.migrations.versions import disable_retired_worker_schedules as migration

RETIRED = {"personal", "planning-day", "planning-midday-check", "planning-sprint"}
SPECIALISTS = {
    "panels-worker-personal-task",
    "panels-worker-planning-day",
    "panels-worker-planning-midday-check",
    "panels-worker-planning-sprint",
}




def test_schedule_migration_preserves_tickets_receipts_and_unrelated_schedules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = sa.create_engine("sqlite://")
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE scheduled_ticket_schedules("
            "id TEXT,worker_type TEXT,enabled INT,definition TEXT)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE tickets(id TEXT,worker_type TEXT,stage TEXT,field_values TEXT)"
        )
        conn.exec_driver_sql("CREATE TABLE scheduled_ticket_occurrences(id TEXT,receipt TEXT)")
        for worker in (*sorted(RETIRED), "coding", "general"):
            for enabled in (0, 1):
                conn.exec_driver_sql(
                    "INSERT INTO scheduled_ticket_schedules VALUES(?,?,?,?)",
                    (f"{worker}-{enabled}", worker, enabled, "owner text"),
                )
            conn.exec_driver_sql(
                "INSERT INTO tickets VALUES(?,?,?,?)",
                (worker, worker, "needs_consequences", json.dumps({"brief": "original"})),
            )
            conn.exec_driver_sql(
                "INSERT INTO scheduled_ticket_occurrences VALUES(?,?)", (worker, "original receipt")
            )
        before_tickets = conn.exec_driver_sql("SELECT * FROM tickets").all()
        before_receipts = conn.exec_driver_sql("SELECT * FROM scheduled_ticket_occurrences").all()
        before_schedules = conn.exec_driver_sql("SELECT * FROM scheduled_ticket_schedules").all()
        monkeypatch.setattr(op, "get_bind", lambda: conn)
        migration.upgrade()
        migration.upgrade()
        assert conn.exec_driver_sql("SELECT * FROM tickets").all() == before_tickets
        assert (
            conn.exec_driver_sql("SELECT * FROM scheduled_ticket_occurrences").all()
            == before_receipts
        )
        assert [
            tuple(row) for row in conn.exec_driver_sql("SELECT * FROM scheduled_ticket_schedules")
        ] == [
            (row[0], row[1], 0 if row[1] in RETIRED else row[2], row[3]) for row in before_schedules
        ]


def test_reopen_preserves_installed_retired_definitions_and_owner_specialists(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "planner.db")
    conn = connect(path)
    create_schema(conn)
    historical = json.loads(
        (Path(__file__).parents[1] / "support" / "retired_worker_types.json").read_text()
    )
    for position, definition in enumerate(historical, start=100):
        definition["fields"] = [
            field for field in definition["fields"] if field["id"] != "retirement"
        ]
        conn.execute(
            "INSERT INTO worker_types (worker_type, position, definition_json, updated_at) "
            "VALUES (?,?,?,?)",
            (definition["worker_type"], position, json.dumps(definition), 123),
        )
        name = definition["profile"]["specialist_skill"]
        source = f"---\nname: {name}\ndescription: Owner specialist\n---\n\nOwner custom prose.\n"
        conn.execute(
            "INSERT INTO managed_skills(skill_name,source_text,updated_at) VALUES (?,?,?)",
            (name, source, 456),
        )
    conn.commit()
    before_types = conn.execute("SELECT * FROM worker_types ORDER BY worker_type").fetchall()
    before_skills = conn.execute("SELECT * FROM managed_skills ORDER BY skill_name").fetchall()
    conn.close()
    conn = connect(path)
    try:
        create_schema(conn)
        assert (
            conn.execute("SELECT * FROM worker_types ORDER BY worker_type").fetchall()
            == before_types
        )
        assert (
            conn.execute("SELECT * FROM managed_skills ORDER BY skill_name").fetchall()
            == before_skills
        )
    finally:
        conn.close()
