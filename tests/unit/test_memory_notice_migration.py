"""Cutover holds preserve unknown history without fabricating delivery."""

import shutil
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from tests.unit.test_worker_memory import _add_prompt, _add_ticket, _make_conversation

from planner.core import db
from planner.core.db import connect, create_schema
from planner.core.migrations.versions import consume_memory_notice_authorization as migration
from planner.runtime import worker_memory


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
    with patch.object(db, "MIGRATIONS_DIRECTORY", tree), connect(path) as conn:
        create_schema(conn)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == revision
    return path


def _legacy_database(tmp_path: Path) -> str:
    return _database_at_revision(tmp_path, "scheduled_ticket_locale")


def test_cutover_holds_known_events_missing_events_and_former_links(tmp_path: Path) -> None:
    path = _legacy_database(tmp_path)
    with connect(path) as conn:
        for index, mode in enumerate(("known", "missing", "answered", "event_only", "former")):
            _make_conversation(conn, mode, compacted=7)
            _add_prompt(conn, mode, sequence=3, sender_message_id="worker_step_message_initial")
            _add_ticket(conn, f"t_{index}", claim="out", conversation_id=mode)
        conn.execute(
            "DELETE FROM conversation_events WHERE conversation_id='missing' "
            "AND kind='context_compacted'"
        )
        _add_prompt(conn, "answered", sequence=9, sender_message_id="worker_step_message_answer")
        # Coverage can lag a confirmed immutable event.
        conn.execute(
            "UPDATE conversations SET automatically_compacted_through_sequence=2 "
            "WHERE conversation_id='event_only'"
        )
        conn.execute(
            "INSERT INTO conversation_events VALUES ('known',9,'context_compacted','{}',1)"
        )
        conn.execute("INSERT INTO ticket_conversations VALUES ('former','t_4')")
        conn.execute("UPDATE tickets SET conversation_id=NULL WHERE id='t_4'")
    with connect(path) as conn:
        create_schema(conn)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "merge_executor_memory_notice"
        )
        assert conn.execute("SELECT * FROM executor_connections").fetchall() == []
        rows = conn.execute(
            "SELECT conversation_id,context_compacted_sequence FROM worker_memory_notice_holds "
            "ORDER BY conversation_id"
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("event_only", 8),
            ("former", 8),
            ("known", 8),
            ("known", 9),
            ("missing", None),
        ]
        assert (
            conn.execute("SELECT COUNT(*) FROM worker_memory_notice_reservations").fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM conversation_events WHERE kind='prompt'").fetchone()[
                0
            ]
            == 6
        )
        # A missing-history conversation hold blocks later confirmed events.
        conn.execute(
            "INSERT INTO conversation_events VALUES ('missing',12,'context_compacted','{}',1)"
        )
        assert worker_memory.ticket_ids_holding_an_unanswered_memory_loss(conn) == ()
        create_schema(conn)
        assert conn.execute("SELECT COUNT(*) FROM worker_memory_notice_holds").fetchone()[0] == 5


def test_failed_cutover_rolls_back_tables_and_holds(tmp_path: Path) -> None:
    path = _legacy_database(tmp_path)
    engine = db._migration_engine(path, 5000)
    try:
        with pytest.raises(RuntimeError, match="synthetic cutover failure"):
            with engine.begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
                    raise RuntimeError("synthetic cutover failure")
    finally:
        engine.dispose()
    with connect(path) as conn:
        assert (
            conn.execute(
                "SELECT name FROM sqlite_master WHERE name LIKE 'worker_memory_notice_%'"
            ).fetchall()
            == []
        )
        with pytest.raises(sqlite3.OperationalError):
            worker_memory.require_memory_notice_storage(conn)
        create_schema(conn)
        worker_memory.require_memory_notice_storage(conn)


@pytest.mark.parametrize(
    "previous_head", ["executor_authoritative_acceptance", "consume_memory_notice_authorization"]
)
def test_combined_migration_upgrades_either_reviewed_head_without_losing_rows(
    tmp_path: Path, previous_head: str
) -> None:
    path = _database_at_revision(tmp_path, previous_head)
    with connect(path) as conn:
        _make_conversation(conn, "preserved", compacted=7)
        _add_ticket(conn, "t_preserved", claim="out", conversation_id="preserved")
        ticket_before = dict(
            conn.execute("SELECT * FROM tickets WHERE id='t_preserved'").fetchone()
        )
        if previous_head == "executor_authoritative_acceptance":
            conn.execute("INSERT INTO executor_connections VALUES ('retained',1,'{}','lost',1,1,0)")
        else:
            conn.execute(
                "INSERT INTO worker_memory_notice_reservations VALUES "
                "('t_preserved','preserved',8,'retained-message',1)"
            )
            conn.execute(
                "INSERT INTO worker_memory_notice_holds VALUES "
                "('t_preserved','preserved',NULL,2,'legacy_unresolved_boundary')"
            )
    with connect(path) as conn:
        create_schema(conn)
        assert conn.execute("SELECT version_num FROM alembic_version").fetchall()[0][0] == (
            "merge_executor_memory_notice"
        )
        assert dict(conn.execute("SELECT * FROM tickets WHERE id='t_preserved'").fetchone()) == (
            ticket_before
        )
        if previous_head == "executor_authoritative_acceptance":
            assert tuple(conn.execute("SELECT * FROM executor_connections").fetchone()) == (
                "retained",
                1,
                "{}",
                "lost",
                1,
                1,
                0,
            )
            assert tuple(
                conn.execute(
                    "SELECT ticket_id,conversation_id,context_compacted_sequence "
                    "FROM worker_memory_notice_holds"
                ).fetchone()
            ) == ("t_preserved", "preserved", 8)
        else:
            assert tuple(
                conn.execute("SELECT * FROM worker_memory_notice_reservations").fetchone()
            ) == ("t_preserved", "preserved", 8, "retained-message", 1)
            assert conn.execute("SELECT * FROM executor_connections").fetchall() == []
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        create_schema(conn)
        if previous_head == "consume_memory_notice_authorization":
            assert [
                tuple(row) for row in conn.execute("SELECT * FROM worker_memory_notice_holds")
            ] == [("t_preserved", "preserved", None, 2, "legacy_unresolved_boundary")]
