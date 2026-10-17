"""Cutover holds preserve unknown history without fabricating delivery."""

import sqlite3
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.unit.test_worker_memory import _add_prompt, _add_ticket, _make_conversation

from planner.core import db
from planner.core.db import connect, create_schema
from planner.core.migrations.versions import consume_memory_notice_authorization as migration
from planner.runtime import worker_memory


def _legacy_database(tmp_path: Path) -> str:
    path = str(tmp_path / "legacy.db")
    with connect(path) as conn:
        create_schema(conn)
        conn.execute("DROP TABLE worker_memory_notice_holds")
        conn.execute("DROP TABLE worker_memory_notice_reservations")
        conn.execute("UPDATE alembic_version SET version_num='scheduled_ticket_locale'")
    return path


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
