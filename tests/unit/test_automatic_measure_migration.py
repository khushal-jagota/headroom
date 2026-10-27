"""Upgrade existing wake identities, delivery members, indices, and sequence intact."""

import shutil
from contextlib import closing
from pathlib import Path

import pytest
from tests.unit.test_db import _migration_tree_through

from planner.core import db
from planner.measure_sources.resource_versions import SOURCE_TRIGGERS


@pytest.mark.parametrize("retain_notice", [True, False])
def test_automatic_measures_upgrade_preserves_existing_notice_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    retain_notice: bool,
) -> None:
    shipped = db.MIGRATIONS_DIRECTORY
    tree = _migration_tree_through(tmp_path, monkeypatch, "owner_message_preview")
    with closing(db.connect(str(tmp_path / "before-measures.db"))) as conn:
        db.create_schema(conn)
        conn.execute(
            "INSERT INTO manager_wakes(id,source_kind,source_revision,summary,created_at,"
            "target_kind,target_id) VALUES (7,'turn_failure',1,'retained',1,'chief','chief')"
        )
        conn.execute(
            "INSERT INTO manager_wake_batches(id,sender_message_id,message,status,created_at,"
            "updated_at,target_kind,target_id) VALUES (3,'retained',"
            "'retained','pending',1,1,'chief','chief')"
        )
        conn.execute("INSERT INTO manager_wake_batch_members(batch_id,wake_id) VALUES (3,7)")
        conn.execute(
            "INSERT INTO manager_wakes(id,source_kind,source_revision,summary,created_at,"
            "target_kind,target_id) VALUES (100,'turn_failure',2,'deleted',1,'chief','chief')"
        )
        conn.execute("DELETE FROM manager_wakes WHERE id=100")
        if not retain_notice:
            conn.execute("DELETE FROM manager_wakes WHERE id=7")
        conn.execute(
            "CREATE TRIGGER fixture_wake_updated AFTER UPDATE ON manager_wakes BEGIN SELECT 1; END"
        )
        wake_row = conn.execute("SELECT * FROM manager_wakes WHERE id=7").fetchone()
        wake = None if wake_row is None else tuple(wake_row)
        batch = tuple(conn.execute("SELECT * FROM manager_wake_batches WHERE id=3").fetchone())
        objects = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='manager_wakes'")
        }
        conn.commit()
        shutil.copy(
            shipped / "versions/automatic_project_measures.py",
            tree / "versions/automatic_project_measures.py",
        )
        db.create_schema(conn)
        if retain_notice:
            assert wake is not None
            assert tuple(conn.execute("SELECT * FROM manager_wakes WHERE id=7").fetchone()) == (
                *wake,
                None,
                None,
                None,
            )
            assert tuple(conn.execute("SELECT * FROM manager_wake_batch_members").fetchone()) == (
                3,
                7,
            )
        else:
            assert conn.execute("SELECT * FROM manager_wakes").fetchall() == []
            assert conn.execute("SELECT * FROM manager_wake_batch_members").fetchall() == []
        assert (
            tuple(conn.execute("SELECT * FROM manager_wake_batches WHERE id=3").fetchone()) == batch
        )
        assert objects <= {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE tbl_name='manager_wakes'")
        }
        inserted = conn.execute(
            "INSERT INTO manager_wakes(source_kind,source_revision,summary,"
            "created_at,target_kind,target_id) "
            "VALUES ('turn_failure',3,'after',1,'chief','chief')"
        )
        assert inserted.lastrowid == 101
        triggers = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
        }
        assert {item.name for item in SOURCE_TRIGGERS} <= triggers
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
