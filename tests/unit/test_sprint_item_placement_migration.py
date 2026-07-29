"""Focused coverage for the sprint-item-only placement migration."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "day_midday_reconciliation"
HEAD_REVISION = "notifications"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def _insert_project(conn: sqlite3.Connection, project_id: str, name: str) -> None:
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES (?, ?, '', 1, 1)",
        (project_id, name),
    )


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item_id: str | None = None,
) -> None:
    fields = json.dumps(
        {
            field: {"value": None, "proposal": None, "user_note": None}
            for field in ("kickoff", "success", "approach", "plan", "implementation", "closeout")
        },
        separators=(",", ":"),
    )
    conn.execute(
        "INSERT INTO tickets ("
        "id, title, worker_type, employee_backend, project_id, sprint_item_id, sprint_id, "
        "ceiling, fields, alias, created_at, updated_at"
        ") VALUES (?, ?, 'coding', 'hermes', ?, ?, ?, 'needs_success', ?, ?, 10, 20)",
        (
            ticket_id,
            f"Ticket {ticket_id}",
            project_id,
            sprint_item_id,
            sprint_id,
            fields,
            f"alias-{ticket_id}",
        ),
    )


def _insert_schedule(
    conn: sqlite3.Connection,
    schedule_id: str,
    *,
    project_id: str | None,
    sprint_id: str | None,
    sprint_item_id: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO scheduled_ticket_schedules ("
        "id, enabled, cadence, local_time, title, worker_type, priority, project_id, "
        "sprint_id, sprint_item_id, created_at, updated_at"
        ") VALUES (?, 1, 'every_planning_day', '09:30', ?, 'coding', 'P2', ?, ?, ?, 30, 40)",
        (
            schedule_id,
            f"Schedule {schedule_id}",
            project_id,
            sprint_id,
            sprint_item_id,
        ),
    )


def test_migration_moves_direct_placements_to_shared_other_items_and_preserves_schema(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "placement.db"
    conn = _upgrade_to_previous_revision(db_path)
    for project_id, name in (
        ("project_vylo", "Vylo"),
        ("project_tribe", "Tribe"),
        ("project_other", "Other"),
    ):
        _insert_project(conn, project_id, name)
    conn.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_one', 'One', '2026-07-20', '2026-08-02', 1, 1)"
    )
    conn.execute(
        "INSERT INTO sprint_items ("
        "id, title, project_id, sprint_id, created_at, updated_at"
        ") VALUES ('si_normal', 'Normal', 'project_vylo', 'sp_one', 1, 1)"
    )

    _insert_ticket(conn, "t_vylo_one", project_id="project_vylo", sprint_id="sp_one")
    _insert_ticket(conn, "t_vylo_two", project_id="project_vylo", sprint_id="sp_one")
    _insert_ticket(conn, "t_other", project_id=None, sprint_id="sp_one")
    _insert_ticket(
        conn,
        "t_already_parented",
        project_id="project_tribe",
        sprint_id=None,
        sprint_item_id="si_normal",
    )
    _insert_ticket(conn, "t_backlog", project_id="project_tribe", sprint_id=None)

    conn.execute(
        "INSERT INTO days (id, created_at, updated_at) VALUES ('day_2026-07-28', 1, 1)"
    )
    conn.execute(
        "INSERT INTO day_tickets (day_id, ticket_id, position) "
        "VALUES ('day_2026-07-28', 't_vylo_one', 0)"
    )

    _insert_schedule(
        conn, "schedule_vylo", project_id="project_vylo", sprint_id="sp_one"
    )
    _insert_schedule(conn, "schedule_other", project_id=None, sprint_id="sp_one")
    _insert_schedule(
        conn,
        "schedule_already_parented",
        project_id="project_tribe",
        sprint_id=None,
        sprint_item_id="si_normal",
    )
    _insert_schedule(
        conn, "schedule_project_template", project_id="project_tribe", sprint_id=None
    )
    conn.execute(
        "INSERT INTO scheduled_ticket_occurrences ("
        "schedule_id, occurrence_key, target_day_id, outcome, created_at"
        ") VALUES ('schedule_vylo', '2026-07-28T09:30', 'day_2026-07-28', 'suppressed', 50)"
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
        HEAD_REVISION
    )
    assert "sprint_id" not in {
        str(row["name"]) for row in upgraded.execute("PRAGMA table_info(tickets)")
    }
    assert "sprint_id" not in {
        str(row["name"])
        for row in upgraded.execute("PRAGMA table_info(scheduled_ticket_schedules)")
    }
    kind = {
        str(row["name"]): row
        for row in upgraded.execute("PRAGMA table_info(sprint_items)")
    }["kind"]
    assert (kind["notnull"], kind["dflt_value"]) == (1, "'normal'")

    other_items = upgraded.execute(
        "SELECT id, project_id FROM sprint_items "
        "WHERE sprint_id = 'sp_one' AND kind = 'other' ORDER BY project_id"
    ).fetchall()
    assert [str(row["project_id"]) for row in other_items] == [
        "project_other",
        "project_vylo",
    ]
    other_by_project = {
        str(row["project_id"]): str(row["id"]) for row in other_items
    }
    assert upgraded.execute(
        "SELECT kind FROM sprint_items WHERE id = 'si_normal'"
    ).fetchone()[0] == "normal"

    ticket_rows = {
        str(row["id"]): (row["project_id"], row["sprint_item_id"])
        for row in upgraded.execute(
            "SELECT id, project_id, sprint_item_id FROM tickets ORDER BY id"
        )
    }
    assert ticket_rows["t_vylo_one"] == (None, other_by_project["project_vylo"])
    assert ticket_rows["t_vylo_two"] == (None, other_by_project["project_vylo"])
    assert ticket_rows["t_other"] == (None, other_by_project["project_other"])
    assert ticket_rows["t_already_parented"] == (None, "si_normal")
    assert ticket_rows["t_backlog"] == ("project_tribe", None)

    schedule_rows = {
        str(row["id"]): (
            row["project_id"],
            row["sprint_item_id"],
            row["placement_mode"],
        )
        for row in upgraded.execute(
            "SELECT id, project_id, sprint_item_id, placement_mode "
            "FROM scheduled_ticket_schedules ORDER BY id"
        )
    }
    assert schedule_rows["schedule_vylo"] == (
        None,
        other_by_project["project_vylo"],
        "sprint_item",
    )
    assert schedule_rows["schedule_other"] == (
        None,
        other_by_project["project_other"],
        "sprint_item",
    )
    assert schedule_rows["schedule_already_parented"] == (
        None,
        "si_normal",
        "sprint_item",
    )
    assert schedule_rows["schedule_project_template"] == (
        "project_tribe",
        None,
        "current_sprint",
    )

    assert upgraded.execute("SELECT count(*) FROM tickets").fetchone()[0] == 5
    assert upgraded.execute(
        "SELECT count(*) FROM scheduled_ticket_schedules"
    ).fetchone()[0] == 4
    assert upgraded.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 1
    assert upgraded.execute(
        "SELECT count(*) FROM scheduled_ticket_occurrences"
    ).fetchone()[0] == 1
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []

    ticket_indexes = {
        str(row["name"]) for row in upgraded.execute("PRAGMA index_list(tickets)")
    }
    assert {
        "idx_tickets_alias",
        "idx_tickets_project_id",
        "idx_tickets_stage",
        "idx_tickets_worker_type_stage",
    }.issubset(ticket_indexes)
    schedule_indexes = {
        str(row["name"])
        for row in upgraded.execute("PRAGMA index_list(scheduled_ticket_schedules)")
    }
    assert "idx_scheduled_ticket_schedules_slot" in schedule_indexes
    item_indexes = {
        str(row["name"]) for row in upgraded.execute("PRAGMA index_list(sprint_items)")
    }
    assert "idx_sprint_items_one_other_per_sprint_project" in item_indexes

    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute("UPDATE sprint_items SET kind = 'catch_all' WHERE id = 'si_normal'")
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "INSERT INTO sprint_items ("
            "id, title, project_id, sprint_id, kind, created_at, updated_at"
            ") VALUES ("
            "'si_duplicate_other', 'Duplicate', 'project_vylo', 'sp_one', 'other', 1, 1"
            ")"
        )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "UPDATE scheduled_ticket_schedules SET cadence = 'sometimes' "
            "WHERE id = 'schedule_vylo'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "UPDATE scheduled_ticket_schedules SET placement_mode = 'somewhere' "
            "WHERE id = 'schedule_vylo'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "UPDATE tickets SET ticket_status = 'mystery' WHERE id = 't_vylo_one'"
        )
    upgraded.close()


def test_migration_reconciliation_failure_rolls_back_every_change(tmp_path: Path) -> None:
    db_path = tmp_path / "collision.db"
    conn = _upgrade_to_previous_revision(db_path)
    _insert_project(conn, "project_someone_elses_other", "Other")
    conn.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_one', 'One', '2026-07-20', '2026-08-02', 1, 1)"
    )
    _insert_ticket(conn, "t_needs_other", project_id=None, sprint_id="sp_one")
    conn.close()

    failed = connect(str(db_path))
    with pytest.raises(RuntimeError, match="canonical Other project id is absent"):
        create_schema(failed)

    assert failed.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
        PREVIOUS_REVISION
    )
    assert "kind" not in {
        str(row["name"]) for row in failed.execute("PRAGMA table_info(sprint_items)")
    }
    assert "sprint_id" in {
        str(row["name"]) for row in failed.execute("PRAGMA table_info(tickets)")
    }
    row = failed.execute(
        "SELECT project_id, sprint_id, sprint_item_id FROM tickets "
        "WHERE id = 't_needs_other'"
    ).fetchone()
    assert tuple(row) == (None, "sp_one", None)
    assert failed.execute("PRAGMA foreign_key_check").fetchall() == []
    failed.close()
