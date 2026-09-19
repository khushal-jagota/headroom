"""The obsolete generic worker context has a forward-only removal migration."""

from __future__ import annotations

from pathlib import Path

from alembic import command

from planner.core import db as db_module
from planner.core.db import connect, create_schema

PREVIOUS_REVISION = "remove_ticket_alias_and_backend_error"
HEAD_REVISION = "worker_types_in_database"


def test_migration_drops_every_pending_worker_context_row(tmp_path: Path) -> None:
    db_path = tmp_path / "drop-pending-worker-context.db"
    _upgrade_to_previous_revision(db_path)
    before = connect(str(db_path))
    before.execute(
        "INSERT INTO pending_worker_context "
        "(worker_entity_id,context_key,text,revision) VALUES "
        "('t_existing','ticket_changed','Reread this Ticket.',4)"
    )
    before.execute(
        "INSERT INTO pending_worker_context "
        "(worker_entity_id,context_key,text,revision) VALUES "
        "('t_other','day_changed','This Ticket is on today.',1)"
    )
    before.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)

    assert (
        upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0] == HEAD_REVISION
    )
    assert (
        upgraded.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pending_worker_context'"
        ).fetchone()
        is None
    )
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    upgraded.close()


def _upgrade_to_previous_revision(path: Path) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
