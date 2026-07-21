"""The day data layer. Materializes a day on first read/write (no "missing day"
state), owns the ordered day-ticket list (contiguous positions from 0), and the
overview-field/notes writes. Every state transition here appends exactly one
canonical event. No FastAPI/pydantic; times come in as unix-second ints from the
caller's clock."""

from __future__ import annotations

import sqlite3

from planner.core.contracts import EventKind
from planner.core.events import append_event
from planner.days.contracts import Day, DayTicket


def materialize_day(conn: sqlite3.Connection, day_id: str, now_unix: int) -> None:
    """§3.4: create the day row if absent (all overview fields + notes = '',
    created_at=updated_at=now_unix) and append a
    day_created event. Idempotent: a present day → no write, no event."""
    if conn.execute("SELECT 1 FROM days WHERE id = ?", (day_id,)).fetchone() is not None:
        return
    conn.execute(
        "INSERT INTO days (id, focus, brief_take, watchout, if_today_lands, notes, "
        "created_at, updated_at) VALUES (?, '', '', '', '', '', ?, ?)",
        (day_id, now_unix, now_unix),
    )
    append_event(conn, day_id, EventKind.day_created, {}, now_unix)


def read_day(conn: sqlite3.Connection, day_id: str, now_unix: int) -> Day:
    """§3.4 'reading a nonexistent day materializes it empty'. Materializes, then
    SELECTs the row and builds a Day."""
    materialize_day(conn, day_id, now_unix)
    row = conn.execute(
        "SELECT id, focus, brief_take, watchout, if_today_lands, notes, "
        "created_at, updated_at "
        "FROM days WHERE id = ?",
        (day_id,),
    ).fetchone()
    assert row is not None
    return Day(
        id=row["id"],
        focus=row["focus"],
        brief_take=row["brief_take"],
        watchout=row["watchout"],
        if_today_lands=row["if_today_lands"],
        notes=row["notes"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_day_tickets(conn: sqlite3.Connection, day_id: str) -> list[DayTicket]:
    """day_tickets for the day, ordered by position (contiguous from 0)."""
    rows = conn.execute(
        "SELECT day_id, ticket_id, position FROM day_tickets WHERE day_id = ? ORDER BY position",
        (day_id,),
    ).fetchall()
    return [
        DayTicket(day_id=row["day_id"], ticket_id=row["ticket_id"], position=row["position"])
        for row in rows
    ]


def add_day_ticket(
    conn: sqlite3.Connection, day_id: str, ticket_id: str, now_unix: int, cause: str = "manual"
) -> bool:
    """§3.4: append at end with the next contiguous position (= current count).
    Idempotent: if (day_id, ticket_id) already present → return False, no event.
    On add → INSERT, append day_ticket_added {ticket_id, position, cause}, bump
    updated_at, return True. Materializes the day first (write path)."""
    materialize_day(conn, day_id, now_unix)
    present = conn.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (day_id, ticket_id),
    ).fetchone()
    if present is not None:
        return False
    position = conn.execute(
        "SELECT COUNT(*) AS n FROM day_tickets WHERE day_id = ?", (day_id,)
    ).fetchone()["n"]
    conn.execute(
        "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, ?)",
        (day_id, ticket_id, position),
    )
    append_event(
        conn,
        day_id,
        EventKind.day_ticket_added,
        {"ticket_id": ticket_id, "position": position, "cause": cause},
        now_unix,
    )
    conn.execute("UPDATE days SET updated_at = ? WHERE id = ?", (now_unix, day_id))
    return True


def remove_day_ticket(
    conn: sqlite3.Connection, day_id: str, ticket_id: str, now_unix: int
) -> bool:
    """§3.4: delete the association only (Ticket untouched). If nothing was
    deleted (rowcount 0) → no-op, no event, no re-pack. Else re-pack remaining
    positions to 0..n-1 in existing position order, append day_ticket_removed
    {ticket_id}, bump updated_at, and return True. The absent-row no-op returns False."""
    cursor = conn.execute(
        "DELETE FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (day_id, ticket_id),
    )
    if cursor.rowcount == 0:
        return False
    survivors = conn.execute(
        "SELECT ticket_id FROM day_tickets WHERE day_id = ? ORDER BY position",
        (day_id,),
    ).fetchall()
    for new_position, row in enumerate(survivors):
        conn.execute(
            "UPDATE day_tickets SET position = ? WHERE day_id = ? AND ticket_id = ?",
            (new_position, day_id, row["ticket_id"]),
        )
    append_event(conn, day_id, EventKind.day_ticket_removed, {"ticket_id": ticket_id}, now_unix)
    conn.execute("UPDATE days SET updated_at = ? WHERE id = ?", (now_unix, day_id))
    return True


# The direct-editable day text fields (the four overview fields + notes). The api
# validates the field name against this set before calling set_day_field, so the
# column name is safe to interpolate.
DAY_TEXT_FIELDS = ("focus", "brief_take", "watchout", "if_today_lands", "notes")


def set_day_field(
    conn: sqlite3.Connection, day_id: str, field: str, value: str, now_unix: int
) -> None:
    """Manual per-field edit of one day text field (§3.4): materialize, UPDATE that
    column + updated_at, append day_updated {field}. `field` must be one of
    DAY_TEXT_FIELDS (api-validated) — the guard keeps the interpolated column safe."""
    if field not in DAY_TEXT_FIELDS:
        raise ValueError(f"not a day text field: {field}")
    materialize_day(conn, day_id, now_unix)
    conn.execute(
        f"UPDATE days SET {field} = ?, updated_at = ? WHERE id = ?", (value, now_unix, day_id)
    )
    append_event(conn, day_id, EventKind.day_updated, {"field": field}, now_unix)
