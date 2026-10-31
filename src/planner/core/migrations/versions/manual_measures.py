"""Retain real source provenance and add explicit manual observations."""

import re
import sqlite3
from typing import cast

from alembic import op

revision = "manual_measures"
down_revision = "goal_conversations"
branch_labels = None
depends_on = None


def _rebuild(
    conn: sqlite3.Connection, table: str, replacements: tuple[tuple[str, str], ...]
) -> None:
    original = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
    )
    sql = re.sub(r'^CREATE TABLE\s+"?' + table + r'"?', f"CREATE TABLE {table}_manual", original)
    for old, new in replacements:
        if sql.count(old) != 1:
            raise RuntimeError(f"{table} schema did not match the manual measure extension")
        sql = sql.replace(old, new, 1)
    declarations = [
        str(row[0])
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL",
            (table,),
        )
    ]
    columns = ",".join(
        '"' + str(row[1]) + '"' for row in conn.execute(f"PRAGMA table_info({table})")
    )
    conn.execute(sql)
    conn.execute(f"INSERT INTO {table}_manual({columns}) SELECT {columns} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_manual RENAME TO {table}")
    for declaration in declarations:
        conn.execute(declaration)


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    # The migration runner disables foreign keys for table swaps and checks all
    # inbound references before committing the migration transaction.
    _rebuild(
        conn,
        "measures",
        (
            ("origin IN ('taken','assembled')", "origin IN ('taken','assembled','manual')"),
            (
                "(origin='assembled' AND source_id IS NULL AND formula IS NOT NULL)",
                "(origin='assembled' AND source_id IS NULL AND formula IS NOT NULL) OR "
                "(origin='manual' AND source_id IS NULL AND formula IS NULL)",
            ),
            (
                "created_at INTEGER NOT NULL",
                "write_revision INTEGER NOT NULL DEFAULT 0 CHECK(write_revision>=0), created_at INTEGER NOT NULL",
            ),
        ),
    )
    conn.execute("""CREATE TABLE manual_measure_writes (
      request_id TEXT PRIMARY KEY, measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE RESTRICT,
      actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL, payload_fingerprint TEXT NOT NULL,
      date TEXT NOT NULL, value REAL NOT NULL,
      write_revision INTEGER NOT NULL CHECK(write_revision>0), accepted_at INTEGER NOT NULL,
      UNIQUE(measure_id,request_id))""")
    _rebuild(
        conn,
        "measure_observations",
        (
            ("run_id TEXT NOT NULL REFERENCES", "run_id TEXT REFERENCES"),
            ("period_start_date TEXT", "manual_write_id TEXT, period_start_date TEXT"),
            (
                "PRIMARY KEY(measure_id,date)",
                "CHECK((run_id IS NOT NULL)+(manual_write_id IS NOT NULL)=1), "
                "FOREIGN KEY(measure_id,manual_write_id) REFERENCES manual_measure_writes(measure_id,request_id) ON DELETE RESTRICT, "
                "PRIMARY KEY(measure_id,date)",
            ),
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Manual measures are forward-only. Restore the matching backup.")
