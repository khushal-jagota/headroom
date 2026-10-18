"""Synthetic on-disk migration, rebuild, restore, and transaction inclusion proof."""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from alembic.script import ScriptDirectory

from planner.core import db
from planner.navigation import rebuild, snapshot
from planner.navigation.proof import capture_command_commits, certify, response_fence


def historical_snapshot(conn: sqlite3.Connection) -> dict[str, Any]:
    """Historical rows remain inspectable while current public reads reject schema 2."""
    scope, version, epoch, revision = conn.execute(
        "SELECT scope,schema_version,epoch,revision FROM navigation_meta"
    ).fetchone()
    return {
        "scope": scope,
        "schema_version": version,
        "epoch": epoch,
        "revision": str(revision),
        "records": [
            {"kind": kind, "id": identity, "value": json.loads(value)}
            for kind, identity, value in conn.execute(
                "SELECT kind,id,value FROM navigation_records ORDER BY kind,id"
            )
        ],
    }


def historical_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> sqlite3.Connection:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = tmp_path / "historical-migrations"
    (tree / "versions").mkdir(parents=True)
    shutil.copy(shipped / "env.py", tree / "env.py")
    for revision in ScriptDirectory(str(shipped)).walk_revisions(
        base="base", head="compact_navigation_lifecycle"
    ):
        shutil.copy(revision.path, tree / "versions" / Path(revision.path).name)
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS_DIRECTORY", tree)
        conn = db.connect(str(tmp_path / "synthetic.db"))
        db.create_schema(conn)
    return conn


def insert_project(conn: sqlite3.Connection, identity: str) -> None:
    conn.execute(
        "INSERT INTO projects(id,name,created_at,updated_at) VALUES(?,?,1,1)",
        (identity, identity),
    )


def test_additive_upgrade_second_open_and_rebuild_preserve_witnesses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = historical_database(tmp_path, monkeypatch)
    insert_project(conn, "before")
    before = historical_snapshot(conn)
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='navigation_commit_witnesses'"
        ).fetchone()
        is None
    )
    db.create_schema(conn)
    after = snapshot(conn)
    assert after["scope"] == before["scope"]
    assert after["records"] == before["records"]
    assert after["epoch"] != before["epoch"]
    assert after["revision"] == "0"
    with capture_command_commits(str(tmp_path / "synthetic.db")) as capture:
        insert_project(conn, "after")
    fence = response_fence(conn, capture)
    assert fence["coverage"] == "complete"
    assert len(fence["witnesses"]) == 1
    witnesses = conn.execute("SELECT identity FROM navigation_commit_witnesses").fetchall()
    conn.close()
    conn = db.connect(str(tmp_path / "synthetic.db"))
    db.create_schema(conn)
    assert conn.execute("SELECT identity FROM navigation_commit_witnesses").fetchall() == witnesses
    with conn:
        rebuild(conn)
    conn.execute("BEGIN")
    result = snapshot(conn)
    assert result["epoch"] != fence["epoch"]
    assert certify(conn, fence)["status"] == "included"
    assert any(record["id"] == "after" for record in result["records"])
    conn.rollback()
    conn.close()


def test_restored_divergent_writes_cannot_certify_lost_command(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.db"
    conn = db.connect(str(path))
    db.create_schema(conn)
    backup_path = tmp_path / "old-backup.db"
    # Both ends are synthetic isolated files. No application backup helper is used.
    backup = sqlite3.connect(backup_path)
    conn.backup(backup)
    backup.close()
    with capture_command_commits(str(path)) as capture:
        insert_project(conn, "lost")
    fence = response_fence(conn, capture)
    conn.close()
    # Restore only after every fixture writer stops.
    for suffix in ("-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    shutil.copy(backup_path, path)
    restored = db.connect(str(path))
    for number in range(5):
        insert_project(restored, f"divergent-{number}")
    restored.execute("BEGIN")
    current = snapshot(restored)
    assert int(current["revision"]) > int(fence["revision"])
    assert certify(restored, fence) == {"status": "unknown", "reason": "missing_witness"}
    restored.rollback()
    restored.close()


def test_failed_additive_migration_rolls_back_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = historical_database(tmp_path, monkeypatch)
    insert_project(conn, "preserved")
    before = historical_snapshot(conn)
    shipped = db.MIGRATIONS_DIRECTORY
    tree = tmp_path / "failed-migrations"
    shutil.copytree(shipped, tree)
    migration = tree / "versions" / "navigation_delivery_proof.py"
    migration.write_text(
        migration.read_text().replace(
            "def upgrade() -> None:",
            "def upgrade() -> None:\n"
            '    op.execute("CREATE TABLE incomplete_delivery(value INTEGER)")\n'
            '    raise RuntimeError("synthetic migration failure")\n',
        )
    )
    with monkeypatch.context() as patch:
        patch.setattr(db, "MIGRATIONS_DIRECTORY", tree)
        with pytest.raises(RuntimeError, match="synthetic migration failure"):
            db.create_schema(conn)
    assert historical_snapshot(conn) == before
    assert (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='incomplete_delivery'").fetchone()
        is None
    )
    assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
        "compact_navigation_lifecycle"
    )
    db.create_schema(conn)
    after = snapshot(conn)
    assert after["scope"] == before["scope"]
    assert after["records"] == before["records"]
    assert after["epoch"] != before["epoch"]
    assert after["revision"] == "0"
    conn.close()
