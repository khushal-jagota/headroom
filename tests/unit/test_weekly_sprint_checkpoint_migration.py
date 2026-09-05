"""Migration coverage for the weekly sprint Checkpoint schedule."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "notification_preferences_by_subject"
CHECKPOINT_SCHEDULE_ID = "schedule_weekly_sprint_checkpoint"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def test_upgrade_preserves_schedules_receipts_and_foreign_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "weekly-sprint.db"
    conn = _upgrade_to_previous_revision(db_path)
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES ('project_existing', 'Existing', '', 1, 2)"
    )
    conn.execute(
        "INSERT INTO scheduled_ticket_schedules ("
        "id, enabled, cadence, local_time, title, worker_type, kickoff_note, priority, "
        "deadline, project_id, sprint_item_id, employee_backend, employee_launch_model, "
        "blocked_by_ticket_ids, created_at, updated_at, placement_mode"
        ") VALUES ("
        "'schedule_existing', 0, 'current_sprint_final_day', '18:45', 'Existing', "
        "'coding', 'Keep me', 'P1', '2026-08-31', 'project_existing', NULL, "
        "'codex', 'model-existing', '[\"t_blocker\"]', 10, 20, 'backlog'"
        ")"
    )
    conn.execute(
        "INSERT INTO scheduled_ticket_occurrences ("
        "schedule_id, occurrence_key, target_day_id, outcome, ticket_id, error, created_at"
        ") VALUES ("
        "'schedule_existing', '2026-08-02T18:45', 'day_2026-08-02', "
        "'failed', NULL, 'kept failure', 30"
        ")"
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    existing = upgraded.execute(
        "SELECT * FROM scheduled_ticket_schedules WHERE id = 'schedule_existing'"
    ).fetchone()
    assert dict(existing) == {
        "id": "schedule_existing",
        "enabled": 0,
        "cadence": "current_sprint_final_day",
        "local_time": "18:45",
        "title": "Existing",
        "worker_type": "coding",
        "kickoff_note": "Keep me",
        "priority": "P1",
        "deadline": "2026-08-31",
        "project_id": "project_existing",
        "sprint_id": None,
        "sprint_item_id": None,
        "employee_backend": "codex",
        "employee_launch_model": "model-existing",
        "blocked_by_ticket_ids": '["t_blocker"]',
        "created_at": 10,
        "updated_at": 20,
        "placement_mode": "backlog",
    }
    receipt = upgraded.execute(
        "SELECT * FROM scheduled_ticket_occurrences "
        "WHERE schedule_id = 'schedule_existing'"
    ).fetchone()
    assert dict(receipt) == {
        "schedule_id": "schedule_existing",
        "occurrence_key": "2026-08-02T18:45",
        "target_day_id": "day_2026-08-02",
        "outcome": "failed",
        "ticket_id": None,
        "error": "kept failure",
        "created_at": 30,
    }
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()


def test_fresh_database_seeds_one_user_owned_checkpoint_without_a_project(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "fresh.db"
    conn = connect(str(db_path))
    create_schema(conn)

    checkpoint = conn.execute(
        "SELECT * FROM scheduled_ticket_schedules WHERE id = ?",
        (CHECKPOINT_SCHEDULE_ID,),
    ).fetchone()
    assert (
        checkpoint["enabled"],
        checkpoint["cadence"],
        checkpoint["local_time"],
        checkpoint["title"],
        checkpoint["worker_type"],
        checkpoint["kickoff_note"],
        checkpoint["priority"],
        checkpoint["project_id"],
        checkpoint["placement_mode"],
    ) == (
        1,
        "current_sprint_day_four",
        "17:00",
        "Checkpoint",
        "personal",
        "Review the sprint so far and decide what to adjust for the remaining days.",
        "P3",
        None,
        "current_sprint",
    )
    assert conn.execute(
        "SELECT count(*) FROM scheduled_ticket_schedules WHERE worker_type = 'personal' "
        "AND title = 'Checkpoint' AND cadence = 'current_sprint_day_four'"
    ).fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE scheduled_ticket_schedules SET cadence = 'sometimes' WHERE id = ?",
            (CHECKPOINT_SCHEDULE_ID,),
        )
    conn.close()
