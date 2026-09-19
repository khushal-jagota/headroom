"""What the removal carries across: no judgments, and no Ticket left at ``dropped``.

The cases are the ones a real database presents at this revision: judgment rows that a
user filled in, a Ticket sitting at ``dropped``, and stored Worker types that still name
the stage.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema
from planner.core.migrations.versions.one_ticket_ending import TROUBLE_BULLET
from planner.managed_skills import read_skill_source
from planner.worker_types.store import read_definitions

PREVIOUS_REVISION = "worker_types_in_database"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def _seed_ticket(conn: sqlite3.Connection, ticket_id: str, stage: str) -> None:
    conn.execute(
        "INSERT INTO tickets "
        "(id, worker_type, employee_backend, stage, title, project_id, ceiling, "
        "field_values, archived_field_content, created_at, updated_at) VALUES "
        "(?, 'coding', 'codex', ?, ?, 'project_test', 'done', ?, ?, 1, 1)",
        (ticket_id, stage, f"Title {ticket_id}", json.dumps({"kickoff": "why"}), "old draft"),
    )


@pytest.fixture
def seeded(tmp_path: Path) -> Path:
    db_path = tmp_path / "one-ending.db"
    conn = _upgrade_to_previous_revision(db_path)
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES ('project_test', 'Test', '', 1, 1)"
    )
    _seed_ticket(conn, "t_dropped", "dropped")
    _seed_ticket(conn, "t_open", "needs_plan")
    conn.execute(
        "INSERT INTO ticket_judgments (ticket_id, verdict_rating, verdict_text) "
        "VALUES ('t_open', 3, 'fine')"
    )
    # The shipped skill text at this revision still told a worker to use the command.
    source = str(
        conn.execute(
            "SELECT source_text FROM managed_skills WHERE skill_name = 'panels-worker'"
        ).fetchone()["source_text"]
    )
    conn.execute(
        "UPDATE managed_skills SET source_text = ? WHERE skill_name = 'panels-worker'",
        (source + "\n" + TROUBLE_BULLET,),
    )
    conn.commit()
    conn.close()
    return db_path


def test_both_judgment_tables_are_gone(seeded: Path) -> None:
    conn = connect(str(seeded))
    create_schema(conn)
    try:
        names = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert "ticket_judgments" not in names
        assert "ticket_judgment_trouble_notes" not in names
    finally:
        conn.close()


def test_a_dropped_ticket_becomes_done_and_keeps_everything_else(seeded: Path) -> None:
    conn = connect(str(seeded))
    create_schema(conn)
    try:
        row = conn.execute(
            "SELECT stage, title, field_values, archived_field_content FROM tickets "
            "WHERE id = 't_dropped'"
        ).fetchone()
        assert str(row["stage"]) == "done"
        assert str(row["title"]) == "Title t_dropped"
        assert json.loads(str(row["field_values"])) == {"kickoff": "why"}
        assert str(row["archived_field_content"]) == "old draft"
        # A Ticket that was not at dropped is untouched.
        assert (
            str(conn.execute("SELECT stage FROM tickets WHERE id = 't_open'").fetchone()["stage"])
            == "needs_plan"
        )
    finally:
        conn.close()


def test_every_stored_worker_type_loses_the_stage_and_still_loads(seeded: Path) -> None:
    conn = connect(str(seeded))
    create_schema(conn)
    try:
        for row in conn.execute("SELECT definition_json FROM worker_types"):
            assert "dropped" not in json.loads(str(row["definition_json"]))
        # Reading them back is the real check: a stored record is validated on an exact
        # set of keys, so a leftover one would refuse to load.
        definitions = read_definitions(conn)
        assert definitions
        for definition in definitions:
            assert "dropped" not in definition.stage_ids()
    finally:
        conn.close()


def test_the_worker_skill_stops_naming_a_command_that_is_gone(seeded: Path) -> None:
    conn = connect(str(seeded))
    create_schema(conn)
    try:
        source = read_skill_source(conn, "panels-worker")
        assert TROUBLE_BULLET not in source
        assert "panels worker request-help" in source
    finally:
        conn.close()
