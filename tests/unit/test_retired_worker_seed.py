"""Catalog retirement changes supply, never historical work or owner skill text."""

import json
from pathlib import Path

from planner.core.db import connect, create_schema

RETIRED = {"personal", "planning-day", "planning-midday-check", "planning-sprint"}
SPECIALISTS = {
    "panels-worker-personal-task",
    "panels-worker-planning-day",
    "panels-worker-planning-midday-check",
    "panels-worker-planning-sprint",
}


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
            "INSERT INTO managed_skills(skill_name,source_text,updated_at) VALUES (?,?,?) "
            "ON CONFLICT(skill_name) DO UPDATE SET "
            "source_text=excluded.source_text, updated_at=excluded.updated_at",
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
