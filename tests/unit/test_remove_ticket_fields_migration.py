"""Focused coverage for removing the unused Ticket payload fields."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from tests.support.historical_tickets import insert_historical_ticket

from planner.core import db as db_module
from planner.core.db import connect

PREVIOUS_REVISION = "proposal_delivery_failures"
HEAD_REVISION = "remove_ticket_alias_and_backend_error"
REMOVED_COLUMNS = {"alias", "backend_error"}


def _upgrade_to_previous_revision(path: Path) -> sqlite3.Connection:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    finally:
        engine.dispose()
    return connect(str(path))


def _upgrade(path: Path, revision: str) -> None:
    engine = db_module._migration_engine(str(path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), revision)
    finally:
        engine.dispose()


def _ticket_rows_without_removed_fields(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    columns = [
        str(row["name"])
        for row in conn.execute("PRAGMA table_info(tickets)")
        if str(row["name"]) not in REMOVED_COLUMNS
    ]
    return [
        tuple(row)
        for row in conn.execute(f"SELECT {','.join(columns)} FROM tickets ORDER BY id")
    ]


def test_migration_drops_only_the_two_fields_and_alias_index(tmp_path: Path) -> None:
    db_path = tmp_path / "remove-ticket-fields.db"
    conn = _upgrade_to_previous_revision(db_path)
    first_id = insert_historical_ticket(conn, "t_first", title="Legacy payload")
    second_id = insert_historical_ticket(conn, "t_second", title="Dependent row")
    conn.execute(
        "UPDATE tickets SET alias='old-ticket-42',backend_error='backend stopped',"
        "ticket_status='errored' WHERE id=?",
        (first_id,),
    )
    conn.execute("INSERT INTO days(id,created_at,updated_at) VALUES ('day_test',1,1)")
    conn.execute(
        "INSERT INTO day_tickets(day_id,ticket_id,position) VALUES ('day_test',?,0)",
        (first_id,),
    )
    conn.execute(
        "INSERT INTO links(from_id,to_id,kind) VALUES (?,?,'blocks')",
        (first_id, second_id),
    )
    rows_before = _ticket_rows_without_removed_fields(conn)
    conn.close()

    _upgrade(db_path, HEAD_REVISION)
    upgraded = connect(str(db_path))

    revision = upgraded.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    assert revision == HEAD_REVISION
    assert _ticket_rows_without_removed_fields(upgraded) == rows_before
    assert REMOVED_COLUMNS.isdisjoint(
        {str(row["name"]) for row in upgraded.execute("PRAGMA table_info(tickets)")}
    )
    indexes = {str(row["name"]) for row in upgraded.execute("PRAGMA index_list(tickets)")}
    assert "idx_tickets_alias" not in indexes
    assert {
        "idx_tickets_project_id",
        "idx_tickets_stage",
        "idx_tickets_worker_type_stage",
    }.issubset(indexes)
    assert upgraded.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 1
    assert upgraded.execute("SELECT count(*) FROM links").fetchone()[0] == 1
    assert upgraded.execute("PRAGMA foreign_key_check").fetchall() == []
    # Read the column, not a Ticket: this revision is far behind head, and the current
    # readers ask questions of tables it does not have yet.
    assert (
        upgraded.execute(
            "SELECT ticket_status FROM tickets WHERE id = ?", (first_id,)
        ).fetchone()[0]
        == "errored"
    )
    upgraded.close()
