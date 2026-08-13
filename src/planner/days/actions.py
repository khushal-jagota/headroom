"""Day membership actions that own the transaction their two writes share."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from planner.days import data as days_data
from planner.tickets import data as tickets_data


def add_ticket_to_day(
    conn: sqlite3.Connection,
    day_id: str,
    ticket_id: str,
    *,
    now: int,
    admit: Callable[[], None] | None = None,
) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if admit is not None:
            admit()
        tickets_data.read_ticket(conn, ticket_id)
        changed = days_data.add_day_ticket(conn, day_id, ticket_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    return changed


def remove_ticket_from_day(
    conn: sqlite3.Connection,
    day_id: str,
    ticket_id: str,
    *,
    now: int,
    admit: Callable[[], None] | None = None,
) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if admit is not None:
            admit()
        changed = days_data.remove_day_ticket(conn, day_id, ticket_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    return changed
