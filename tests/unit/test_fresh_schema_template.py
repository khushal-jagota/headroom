"""A test database copied from the per-process template is the database a replay builds."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tests.support import fresh_schema_template

from planner.core import db

# The cells a replay draws fresh for every database, which each copy draws again.
PER_DATABASE_CELLS = {
    "attention_history_metadata": ("started_at",),
    "navigation_meta": ("scope", "epoch"),
    "navigation_delivery_retention": ("epoch",),
    "navigation_proof_identity": ("secret",),
}


Schema = set[tuple[str, str, tuple[str, ...]]]


def _contents(path: Path) -> tuple[Schema, dict[str, list[dict[str, object]]]]:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        # Clauses are compared as a set, because a replay itself writes a rebuilt
        # table's foreign key clauses in no fixed order.
        schema = {
            (
                str(row[0]),
                str(row[1]),
                tuple(sorted(line.strip().rstrip(",") for line in str(row[2]).splitlines())),
            )
            for row in conn.execute(
                "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }
        tables = [name for kind, name, _ in schema if kind == "table"]
        rows = {
            table: sorted(
                (dict(row) for row in conn.execute(f'SELECT * FROM "{table}"')), key=repr
            )
            for table in tables
        }
    return schema, rows


def _without_per_database_cells(
    rows: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    return {
        table: [
            {key: value for key, value in row.items()
             if key not in PER_DATABASE_CELLS.get(table, ())}
            for row in table_rows
        ]
        for table, table_rows in rows.items()
    }


def test_copied_database_matches_a_replay_and_draws_its_own_identity(tmp_path: Path) -> None:
    # Risk: every database-backed test runs against the copy, so a copy that drifted
    # from what the shipped migrations build, or that shared one database's identity
    # with the next, would go unnoticed everywhere else.
    assert db.upgrade_schema_connection is fresh_schema_template.upgrade_schema_connection
    replayed = tmp_path / "replayed.db"
    with db.schema_migration_transaction(str(replayed)) as connection:
        fresh_schema_template.replay_migrations(connection, "head")
    started = int(time.time())
    copies = [tmp_path / "first.db", tmp_path / "second.db"]
    for path in copies:
        db._upgrade_to_current_schema(str(path), 5000)
    finished = int(time.time())

    replayed_schema, replayed_rows = _contents(replayed)
    first, second = (_contents(path) for path in copies)
    for schema, rows in (first, second):
        assert schema == replayed_schema
        # A migration that draws a new per-database value fails here. Copies would
        # share that value, so draw it in _give_the_copy_its_own_identity as well.
        assert _without_per_database_cells(rows) == _without_per_database_cells(replayed_rows)
        started_at = rows["attention_history_metadata"][0]["started_at"]
        assert isinstance(started_at, int) and started <= started_at <= finished
    for table, columns in PER_DATABASE_CELLS.items():
        for column in columns:
            if column != "started_at":
                assert first[1][table][0][column] != second[1][table][0][column]
