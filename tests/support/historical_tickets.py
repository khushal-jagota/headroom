"""Write a Ticket row into a database that is still at an older revision.

A migration test starts before the revision it is testing, so the Ticket rows it seeds
have to be written in the shape the schema had then. The production writers cannot do
that: they write the columns the schema ends with, and a rename between the two makes
the insert fail on a column that does not exist yet.

Only the columns that have carried the same name since the baseline are written here, so
one helper serves every revision a migration test starts from.
"""

from __future__ import annotations

import sqlite3

_EMPTY_FIELDS = "{}"


def insert_historical_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    title: str = "Ticket",
    worker_type: str = "coding",
    employee_backend: str = "codex",
    stage: str = "needs_success",
    ceiling: str = "needs_success",
    ticket_status: str = "empty",
    created_at: int = 1,
    updated_at: int = 1,
) -> str:
    """Insert one Ticket in the shape the connected database currently has."""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(tickets)")}
    fields_column = "fields" if "fields" in columns else "field_values"
    names = [
        "id",
        "title",
        "worker_type",
        "employee_backend",
        "stage",
        "ceiling",
        fields_column,
        "created_at",
        "updated_at",
    ]
    values: list[object] = [
        ticket_id,
        title,
        worker_type,
        employee_backend,
        stage,
        ceiling,
        _EMPTY_FIELDS,
        created_at,
        updated_at,
    ]
    if "ticket_status" in columns:
        names.append("ticket_status")
        values.append(ticket_status)
    if "alias" in columns:
        names.append("alias")
        values.append(f"alias-{ticket_id}")
    placeholders = ",".join("?" for _ in names)
    conn.execute(
        f"INSERT INTO tickets ({','.join(names)}) VALUES ({placeholders})",
        tuple(values),
    )
    return ticket_id
