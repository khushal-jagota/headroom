"""Remove the strategic priority field while retaining all other stored facts."""

from __future__ import annotations

import re

from alembic import op
from sqlalchemy.engine import Connection

revision = "remove_priority"
down_revision = "chief_recap"
branch_labels = None
depends_on = None

_TABLES = ("projects", "sprint_items", "tickets", "scheduled_ticket_schedules")


def _definitions(body: str) -> list[str]:
    """Split CREATE TABLE definitions only at unquoted, outer-level commas."""
    result: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    index = 0
    while index < len(body):
        char = body[index]
        if quote is not None:
            if char == quote:
                if index + 1 < len(body) and body[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in "'\"`[":
            quote = "]" if char == "[" else char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(body[start:index].strip())
            start = index + 1
        index += 1
    if quote is not None or depth != 0:
        raise RuntimeError("Cannot parse stored table definition")
    result.append(body[start:].strip())
    return result


def _remove_priority(conn: Connection, table: str) -> None:
    definition = str(
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).scalar_one()
    )
    opening = definition.index("(")
    closing = definition.rindex(")")
    definitions = _definitions(definition[opening + 1 : closing])
    retained: list[str] = []
    removed_column = False
    for fragment in definitions:
        if re.match(r'^["`\[]?priority["`\]]?\s', fragment, re.IGNORECASE):
            removed_column = True
            continue
        if re.match(r"^(?:CONSTRAINT\s+\S+\s+)?CHECK\s*\(", fragment, re.IGNORECASE):
            if re.search(r"\bpriority\b", fragment, re.IGNORECASE):
                continue
        retained.append(fragment)
    if not removed_column:
        raise RuntimeError(f"{table} does not contain the priority column")
    dependent_sql = [
        str(row[0])
        for row in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE tbl_name=? "
            "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name",
            (table,),
        )
    ]
    columns = ", ".join(
        f'"{row[1]}"'
        for row in conn.exec_driver_sql(f'PRAGMA table_info("{table}")')
        if str(row[1]) != "priority"
    )
    conn.exec_driver_sql(
        f'CREATE TABLE "{table}__without_priority" ('
        + ", ".join(retained)
        + ")"
        + definition[closing + 1 :]
    )
    conn.exec_driver_sql(
        f'INSERT INTO "{table}__without_priority" ({columns}) SELECT {columns} FROM "{table}"'
    )
    conn.exec_driver_sql(f'DROP TABLE "{table}"')
    conn.exec_driver_sql(f'ALTER TABLE "{table}__without_priority" RENAME TO "{table}"')
    for sql in dependent_sql:
        conn.exec_driver_sql(sql)


def upgrade() -> None:
    conn = op.get_bind()
    for table in _TABLES:
        _remove_priority(conn, table)


def downgrade() -> None:
    raise RuntimeError("Removed strategic priority values cannot be restored")
