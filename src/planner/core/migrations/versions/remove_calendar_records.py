"""Delete historical calendars while preserving ordinary work and schedule receipts."""

from __future__ import annotations

import re
from datetime import date

from alembic import op
from sqlalchemy.engine import Connection

from planner.core.migrations.versions.remove_priority import _definitions

revision = "remove_calendar_records"
down_revision = "diagnostic_expiry_metadata"
branch_labels = None
depends_on = None

_CALENDAR_TABLES = ("day_tickets", "sprint_outcomes", "days", "sprints")
_REMOVED_SCHEDULE_COLUMNS = frozenset(("sprint_id", "placement_mode"))


def _validate_receipt_dates(conn: Connection) -> None:
    for schedule_id, occurrence_key, target in conn.exec_driver_sql(
        "SELECT schedule_id,occurrence_key,target_day_id FROM scheduled_ticket_occurrences"
    ):
        try:
            if (
                not isinstance(target, str)
                or re.fullmatch(r"day_\d{4}-\d{2}-\d{2}", target) is None
            ):
                raise ValueError("noncanonical Day identifier")
            if date.fromisoformat(target[4:]).isoformat() != target[4:]:
                raise ValueError("noncanonical calendar date")
        except ValueError as error:
            raise RuntimeError(
                "Calendar removal refused: invalid receipt target "
                f"for {schedule_id!r}/{occurrence_key!r}: {target!r}"
            ) from error


def _remove_schedule_placement(conn: Connection) -> None:
    table = "scheduled_ticket_schedules"
    definition = str(
        conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).scalar_one()
    )
    opening, closing = definition.index("("), definition.rindex(")")
    retained: list[str] = []
    removed: list[str] = []
    for fragment in _definitions(definition[opening + 1 : closing]):
        if re.match(r'^["`\[]?(sprint_id|placement_mode)["`\]]?\s', fragment, re.IGNORECASE):
            removed.append(fragment)
        elif re.match(
            r"^(?:CONSTRAINT\s+\S+\s+)?(?:FOREIGN\s+KEY\s*\(\s*sprint_id\s*\)|"
            r"CHECK\s*\(\s*placement_mode\s+IN\s*\()",
            fragment,
            re.IGNORECASE,
        ):
            removed.append(fragment)
        else:
            retained.append(fragment)
    if len(removed) != 4:
        raise RuntimeError("Schedule placement schema changed; calendar removal needs review")
    if any(re.search(r"\b(sprint_id|placement_mode)\b", value) for value in retained):
        raise RuntimeError("Unexpected surviving schedule placement constraint")
    dependents = [
        str(row[0])
        for row in conn.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE tbl_name=? "
            "AND type IN ('index','trigger') AND sql IS NOT NULL ORDER BY type,name",
            (table,),
        )
    ]
    columns = ",".join(
        '"' + str(row[1]).replace('"', '""') + '"'
        for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
        if str(row[1]) not in _REMOVED_SCHEDULE_COLUMNS
    )
    conn.exec_driver_sql(
        f"CREATE TABLE {table}__without_calendar ("
        + ",".join(retained)
        + ")"
        + definition[closing + 1 :]
    )
    conn.exec_driver_sql(
        f"INSERT INTO {table}__without_calendar ({columns}) SELECT {columns} FROM {table}"
    )
    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"ALTER TABLE {table}__without_calendar RENAME TO {table}")
    for sql in dependents:
        conn.exec_driver_sql(sql)


def _retained_rows(
    conn: Connection, table: str, removed: frozenset[str]
) -> list[tuple[object, ...]]:
    columns = ",".join(
        '"' + str(row[1]).replace('"', '""') + '"'
        for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
        if str(row[1]) not in removed
    )
    return [
        tuple(row) for row in conn.exec_driver_sql(f"SELECT {columns} FROM {table} ORDER BY id")
    ]


def upgrade() -> None:
    conn = op.get_bind()
    # Refuse unknown history before changing any schema or stored value.
    _validate_receipt_dates(conn)
    tickets = _retained_rows(conn, "tickets", frozenset(("sprint_id",)))
    schedules = _retained_rows(conn, "scheduled_ticket_schedules", _REMOVED_SCHEDULE_COLUMNS)
    receipts = [
        (row[0], row[1], str(row[2])[4:], *row[3:])
        for row in conn.exec_driver_sql(
            "SELECT * FROM scheduled_ticket_occurrences ORDER BY schedule_id,occurrence_key"
        )
    ]
    conn.exec_driver_sql("ALTER TABLE tickets DROP COLUMN sprint_id")
    conn.exec_driver_sql(
        "ALTER TABLE scheduled_ticket_occurrences RENAME COLUMN target_day_id TO target_date"
    )
    conn.exec_driver_sql(
        "UPDATE scheduled_ticket_occurrences SET target_date=substr(target_date,5)"
    )
    _remove_schedule_placement(conn)
    for table in _CALENDAR_TABLES:
        conn.exec_driver_sql(f"DROP TABLE {table}")
    if _retained_rows(conn, "tickets", frozenset()) != tickets:
        raise RuntimeError("Calendar removal changed retained Ticket values")
    if _retained_rows(conn, "scheduled_ticket_schedules", frozenset()) != schedules:
        raise RuntimeError("Calendar removal changed retained schedule values")
    if [
        tuple(row)
        for row in conn.exec_driver_sql(
            "SELECT * FROM scheduled_ticket_occurrences ORDER BY schedule_id,occurrence_key"
        )
    ] != receipts:
        raise RuntimeError("Calendar removal changed retained receipt values")
    for name, sql in conn.exec_driver_sql(
        "SELECT name,sql FROM sqlite_master WHERE sql IS NOT NULL"
    ):
        if re.search(r"\b(?:day_tickets|sprint_outcomes|days|sprints)\b", str(sql), re.IGNORECASE):
            raise RuntimeError(f"Calendar removal left a schema reference in {name}")


def downgrade() -> None:
    raise RuntimeError(
        "Calendar deletion is forward-only; restore the verified pre-cutover snapshot"
    )
