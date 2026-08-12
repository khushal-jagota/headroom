"""Focused coverage for the Project folder-path migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "ticket_review_routes"
HEAD_REVISION = "project_folder_path"


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def test_existing_projects_gain_a_nullable_folder_path(tmp_path: Path) -> None:
    db_path = tmp_path / "project-folder.db"
    conn = _upgrade_to_previous_revision(db_path)
    conn.execute(
        "INSERT INTO projects (id, name, summary, priority, created_at, updated_at) "
        "VALUES ('project_existing', 'Existing', 'Kept intact', 'P1', 10, 20)"
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert (
        upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
    )
    existing = upgraded.execute(
        "SELECT name, summary, priority, folder_path, created_at, updated_at "
        "FROM projects WHERE id = 'project_existing'"
    ).fetchone()
    assert existing is not None
    assert tuple(existing) == ("Existing", "Kept intact", "P1", None, 10, 20)
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()
