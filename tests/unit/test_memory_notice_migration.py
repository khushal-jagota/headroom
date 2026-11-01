"""The Mac/VPS migration preserves retained staging data on upgrade and reopen."""

import shutil
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.script import ScriptDirectory
from tests.unit.test_worker_memory import _add_ticket, _make_conversation

from planner.core import db
from planner.core.db import connect, create_schema


def _database_at_revision(tmp_path: Path, revision: str) -> str:
    """Build the actual ancestor schema, without falsely stamping a newer schema."""
    path = str(tmp_path / "legacy.db")
    original = db.MIGRATIONS_DIRECTORY
    tree = tmp_path / "historical-migrations"
    (tree / "versions").mkdir(parents=True)
    shutil.copy(original / "env.py", tree / "env.py")
    for ancestor in ScriptDirectory(str(original)).walk_revisions(base="base", head=revision):
        source = Path(ancestor.path)
        shutil.copy(source, tree / "versions" / source.name)
    with patch.object(db, "MIGRATIONS_DIRECTORY", tree), closing(connect(path)) as conn:
        create_schema(conn)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == revision
    return path


@pytest.mark.parametrize(
    "previous_head",
    ["native_usage_prices", "scheduled_ticket_locale", "video_upload_ledger"],
)
def test_native_usage_merge_preserves_branch_rows_and_reopens(
    tmp_path: Path, previous_head: str
) -> None:
    path = _database_at_revision(tmp_path, previous_head)
    preserved_tables = ["tickets", "conversations", "conversation_events"]
    with closing(connect(path)) as conn:
        _make_conversation(conn, "preserved", compacted=7)
        _add_ticket(conn, "t_preserved", claim="out", conversation_id="preserved")
        if previous_head == "native_usage_prices":
            conn.execute("INSERT INTO native_usage_sources VALUES ('claude','source','{ }')")
            conn.execute("INSERT INTO native_usage_files VALUES ('claude','source','file','{  }')")
            conn.execute(
                "INSERT INTO native_usage_prices VALUES (1,'retained','2026-10-18','{   }')"
            )
            preserved_tables += [
                "native_usage_sources",
                "native_usage_files",
                "native_usage_prices",
            ]
        retained_columns = {
            "tickets": "id,title,worker_type,employee_backend,stage,conversation_id,"
            "worker_step_claim,locale,field_values,pending_proposal",
            "conversations": "conversation_id,backend_key,workspace_folder,access,created_at,"
            "automatically_compacted_through_sequence",
        }
        before = {
            table: [
                tuple(row)
                for row in conn.execute(f"SELECT {retained_columns.get(table, '*')} FROM {table}")
            ]
            for table in preserved_tables
        }
    for _ in range(2):
        with closing(connect(path)) as conn:
            create_schema(conn)
            assert [tuple(row) for row in conn.execute("SELECT * FROM alembic_version")] == [
                (ScriptDirectory(str(db.MIGRATIONS_DIRECTORY)).get_current_head(),)
            ]
            for table, rows in before.items():
                assert [
                    tuple(row)
                    for row in conn.execute(
                        f"SELECT {retained_columns.get(table, '*')} FROM {table}"
                    )
                ] == rows
            if previous_head != "native_usage_prices":
                for table in ("native_usage_sources", "native_usage_files", "native_usage_prices"):
                    assert conn.execute(f"SELECT * FROM {table}").fetchall() == []
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
