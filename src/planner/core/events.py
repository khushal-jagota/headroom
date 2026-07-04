"""The only module that writes or reads the events table. There is no update or
delete path anywhere: events are append-only (§3). The read limit is always
passed in by the caller (from config); this module stays config-free."""

from __future__ import annotations

import json
import sqlite3

from planner.core.contracts import EventKind, EventRow, JsonDict


def append_event(
    conn: sqlite3.Connection,
    entity_id: str,
    kind: EventKind,
    payload: JsonDict,
    created_at: int,
) -> int:
    cursor = conn.execute(
        "INSERT INTO events (entity_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
        (entity_id, kind.value, json.dumps(payload), created_at),
    )
    inserted = cursor.lastrowid
    assert inserted is not None
    return inserted


def read_events_since(conn: sqlite3.Connection, since_id: int, limit: int) -> list[EventRow]:
    rows = conn.execute(
        "SELECT id, entity_id, kind, payload, created_at "
        "FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
        (since_id, limit),
    ).fetchall()
    return [
        EventRow(
            id=row["id"],
            entity_id=row["entity_id"],
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]
