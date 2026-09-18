"""A ceiling's second setting leaves, and every other Ticket value stays."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command

from planner.core import db

SOURCE_REVISION = "two_ownership_modes"
TARGET_REVISION = "drop_ticket_at_cap"


def _upgrade(path: Path, revision: str) -> None:
    engine = db._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db._alembic_config(connection), revision)
    finally:
        engine.dispose()


def _columns(conn: sqlite3.Connection) -> list[str]:
    return [str(row[1]) for row in conn.execute("PRAGMA table_info(tickets)")]


def _insert_ticket(conn: sqlite3.Connection, ticket_id: str, *, at_cap: str) -> None:
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,at_cap,"
        "ticket_status,recap,guidance,field_values,archived_field_content,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            ticket_id,
            f"Ticket {ticket_id}",
            "coding",
            "codex",
            "needs_success",
            "needs_success",
            at_cap,
            "empty",
            f"recap {ticket_id}",
            f"guidance {ticket_id}",
            '{"kickoff":"request"}',
            f"archive {ticket_id}",
            1,
            2,
        ),
    )


def test_the_cap_leaves_and_every_other_ticket_value_is_untouched(tmp_path: Path) -> None:
    path = tmp_path / "cap.db"
    _upgrade(path, SOURCE_REVISION)
    conn = db.connect(str(path))
    _insert_ticket(conn, "t_stop", at_cap="stop")
    _insert_ticket(conn, "t_propose", at_cap="propose")
    retained = (
        "id,title,worker_type,stage,ceiling,ticket_status,recap,guidance,field_values,"
        "archived_field_content,ceiling_holder,created_at,updated_at"
    )
    before = [tuple(row) for row in conn.execute(f"SELECT {retained} FROM tickets ORDER BY id")]
    conn.commit()
    conn.close()

    _upgrade(path, TARGET_REVISION)

    conn = db.connect(str(path))
    try:
        assert "at_cap" not in _columns(conn)
        after = [tuple(row) for row in conn.execute(f"SELECT {retained} FROM tickets ORDER BY id")]
        assert after == before
        # Both Tickets now read the same way: a ceiling, and nothing beside it.
        tickets_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='tickets'"
        ).fetchone()[0]
        assert "at_cap" not in tickets_sql
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
