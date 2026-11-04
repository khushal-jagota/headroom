"""Address source responsibility and durable failure notices to goal agents."""

import re
import sqlite3
from typing import cast

from alembic import op

revision = "goal_source_responsibility"
down_revision = "dimensional_datasets"
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
    sql = re.sub(r'^CREATE TABLE\s+"?' + table + r'"?', f"CREATE TABLE {table}_goal", original)
    for old, new in replacements:
        if sql.count(old) != 1:
            raise RuntimeError(f"{table} schema did not match the goal source extension")
        sql = sql.replace(old, new, 1)
    declarations = tuple(
        str(row[0])
        for row in conn.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name=? "
            "AND type IN ('index','trigger') AND sql IS NOT NULL",
            (table,),
        )
    )
    sequence = conn.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    columns = ",".join(
        '"' + str(row[1]) + '"' for row in conn.execute(f"PRAGMA table_info({table})")
    )
    conn.execute(sql)
    conn.execute(f"INSERT INTO {table}_goal({columns}) SELECT {columns} FROM {table}")
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {table}_goal RENAME TO {table}")
    if sequence is not None:
        changed = conn.execute(
            "UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?", (int(sequence[0]), table)
        )
        if changed.rowcount == 0:
            conn.execute(
                "INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (table, int(sequence[0]))
            )
    for declaration in declarations:
        conn.execute(declaration)


def upgrade() -> None:
    conn = cast(sqlite3.Connection, op.get_bind().connection.driver_connection)
    # The runner disables foreign keys for table swaps and checks all references
    # before the migration transaction commits. Keep the existing table names.
    _rebuild(
        conn,
        "measure_sources",
        (
            (
                "responsible_kind IN ('chief','manager')",
                "responsible_kind IN ('chief','manager','goal')",
            ),
            (
                "responsible_manager_id TEXT REFERENCES managers(id) ON DELETE RESTRICT,",
                "responsible_manager_id TEXT REFERENCES managers(id) ON DELETE RESTRICT, "
                "responsible_goal_id TEXT REFERENCES goals(id) ON DELETE RESTRICT,",
            ),
            (
                "(responsible_kind='chief' AND responsible_manager_id IS NULL)",
                "(responsible_kind='chief' AND responsible_manager_id IS NULL AND responsible_goal_id IS NULL)",
            ),
            (
                "(responsible_kind='manager' AND responsible_manager_id IS NOT NULL)",
                "(responsible_kind='manager' AND responsible_manager_id IS NOT NULL AND responsible_goal_id IS NULL) "
                "OR (responsible_kind='goal' AND responsible_manager_id IS NULL AND responsible_goal_id IS NOT NULL)",
            ),
        ),
    )
    for table in ("manager_wakes", "manager_wake_batches"):
        _rebuild(
            conn,
            table,
            (
                (
                    "target_kind IN ('chief','manager','ticket')",
                    "target_kind IN ('chief','manager','ticket','goal')",
                ),
            ),
        )


def downgrade() -> None:
    raise NotImplementedError("Panels migrations are forward-only")
