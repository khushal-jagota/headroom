"""Focused coverage for the Project-priority migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "sprint_item_only_placement"
HEAD_REVISION = "project_priority"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def test_existing_projects_stay_unassessed_and_the_column_constrains_assessed_values(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "project-priority.db"
    conn = _upgrade_to_previous_revision(db_path)
    conn.execute(
        "INSERT INTO projects (id, name, summary, created_at, updated_at) "
        "VALUES ('project_existing', 'Existing', 'Kept intact', 10, 20)"
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert (
        upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
    )
    existing = upgraded.execute(
        "SELECT name, summary, priority, created_at, updated_at "
        "FROM projects WHERE id = 'project_existing'"
    ).fetchone()
    assert existing is not None
    assert tuple(existing) == ("Existing", "Kept intact", None, 10, 20)
    assert {
        row["id"]: row["priority"]
        for row in upgraded.execute(
            "SELECT id, priority FROM projects WHERE id != 'project_existing'"
        )
    } == {
        "project_other": None,
        "project_tribe": None,
        "project_vylo": None,
    }

    for index, priority in enumerate(("P0", "P1", "P2", "P3")):
        upgraded.execute(
            "INSERT INTO projects "
            "(id, name, summary, priority, created_at, updated_at) "
            "VALUES (?, ?, '', ?, 1, 1)",
            (f"project_valid_{index}", f"Valid {index}", priority),
        )
    with pytest.raises(sqlite3.IntegrityError):
        upgraded.execute(
            "INSERT INTO projects "
            "(id, name, summary, priority, created_at, updated_at) "
            "VALUES ('project_invalid', 'Invalid', '', 'P4', 1, 1)"
        )

    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
