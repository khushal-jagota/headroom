"""Current startup and the transaction guarantees every future migration needs."""

import shutil
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from planner.core import db as db_module
from planner.core.db import BASELINE_REVISION, connect, create_schema


def _revision(conn: sqlite3.Connection) -> str:
    return str(conn.execute("SELECT version_num FROM alembic_version").fetchone()[0])


def _migration_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tree = tmp_path / "migrations"
    shutil.copytree(
        db_module.MIGRATIONS_DIRECTORY, tree, ignore=shutil.ignore_patterns("__pycache__")
    )
    monkeypatch.setattr(db_module, "MIGRATIONS_DIRECTORY", tree)
    return tree


def _append_revision(tree: Path, statements: str) -> None:
    (tree / "versions" / "second.py").write_text(
        'from alembic import op\n'
        'from sqlalchemy import CheckConstraint, Column, Index, Integer, MetaData, Table, Text\n'
        'revision = "second"\n'
        f'down_revision = {BASELINE_REVISION!r}\n'
        'branch_labels = depends_on = None\n'
        '\ndef upgrade():\n'
        + statements
        + '\ndef downgrade():\n    raise NotImplementedError\n',
        encoding="utf-8",
    )


def _parents_and_children(conn: sqlite3.Connection) -> None:
    conn.executescript(
        "CREATE TABLE migration_parent (id TEXT PRIMARY KEY, flag INTEGER NOT NULL "
        "CHECK (flag IN (0,1)));"
        "CREATE INDEX migration_parent_flag ON migration_parent(flag);"
        "CREATE TABLE migration_child (parent_id TEXT REFERENCES migration_parent(id) "
        "ON DELETE CASCADE);"
        "INSERT INTO migration_parent VALUES ('kept',1);"
        "INSERT INTO migration_child VALUES ('kept');"
    )


def test_fresh_database_starts_at_the_single_baseline(tmp_path: Path) -> None:
    conn = connect(str(tmp_path / "fresh.db"))
    try:
        create_schema(conn)
        config = Config()
        config.set_main_option("script_location", str(db_module.MIGRATIONS_DIRECTORY))
        assert ScriptDirectory.from_config(config).get_heads() == [BASELINE_REVISION]
        assert _revision(conn) == BASELINE_REVISION
        assert conn.execute("SELECT count(*) FROM projects").fetchone()[0] > 0
        assert conn.execute("SELECT 1 FROM worker_types WHERE worker_type='coding'").fetchone()
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_current_database_reopens_without_changing_authored_rows(tmp_path: Path) -> None:
    path = str(tmp_path / "current.db")
    conn = connect(path)
    create_schema(conn)
    conn.execute(
        "INSERT INTO tickets (id,title,worker_type,employee_backend,ceiling,field_values,"
        "worker_step_claim,stage,created_at,updated_at) VALUES "
        "('t_kept','Keep owner text','coding','hermes','needs_brief','{}','out','needs_brief',1,2)"
    )
    conn.execute("UPDATE managed_skills SET source_text=source_text || '\nOwner note.'")
    conn.commit()
    before = list(conn.iterdump())
    conn.close()
    conn = connect(path)
    try:
        create_schema(conn)
        assert list(conn.iterdump()) == before
    finally:
        conn.close()


def test_rebuilding_a_table_keeps_children_checks_and_indexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "rebuild.db"))
    try:
        create_schema(conn)
        _parents_and_children(conn)
        _append_revision(
            tree,
            '    table = Table("migration_parent", MetaData(),\n'
            '        Column("id", Text, primary_key=True),\n'
            '        Column("flag", Integer, nullable=False),\n'
            '        CheckConstraint("flag IN (0,1)"))\n'
            '    Index("migration_parent_flag", table.c.flag)\n'
            '    with op.batch_alter_table(\n'
            '        "migration_parent", copy_from=table, recreate="always"):\n'
            '        pass\n',
        )
        create_schema(conn)
        assert _revision(conn) == "second"
        assert tuple(conn.execute("SELECT * FROM migration_parent").fetchone()) == ("kept", 1)
        assert conn.execute("SELECT parent_id FROM migration_child").fetchone()[0] == "kept"
        assert "migration_parent_flag" in {
            row[1] for row in conn.execute("PRAGMA index_list(migration_parent)")
        }
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE migration_parent SET flag=2")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_failed_migration_rolls_back_schema_rows_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "failure.db"))
    try:
        create_schema(conn)
        _parents_and_children(conn)
        before = list(conn.iterdump())
        _append_revision(
            tree,
            '    op.execute("CREATE TABLE half_built (value TEXT)")\n'
            '    op.execute("UPDATE migration_parent SET flag=0")\n'
            '    raise RuntimeError("migration failed halfway")\n',
        )
        with pytest.raises(RuntimeError, match="failed halfway"):
            create_schema(conn)
        assert list(conn.iterdump()) == before
    finally:
        conn.close()


def test_migration_with_a_dangling_reference_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = _migration_tree(tmp_path, monkeypatch)
    conn = connect(str(tmp_path / "orphan.db"))
    try:
        create_schema(conn)
        _parents_and_children(conn)
        before = list(conn.iterdump())
        _append_revision(tree, '    op.execute("DELETE FROM migration_parent")\n')
        with pytest.raises(RuntimeError, match="foreign key violations"):
            create_schema(conn)
        assert list(conn.iterdump()) == before
    finally:
        conn.close()
