"""The only module that writes or reads the events table. Normal history is
append-only. A deliberate entity hard delete may replace that entity's history
with one deletion audit; this module owns that sole pruning path too. The read
limit is always passed in by the caller (from config); this module stays
config-free."""

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


def _contains_entity_id(value: object, entity_id: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_entity_id(item, entity_id) for item in value.values())
    if isinstance(value, list):
        return any(_contains_entity_id(item, entity_id) for item in value)
    return value == entity_id


def delete_entity_history(conn: sqlite3.Connection, entity_id: str) -> None:
    """Prune prior events owned by or referring to one hard-deleted entity."""
    rows = conn.execute("SELECT id, entity_id, payload FROM events").fetchall()
    event_ids = [
        int(row["id"])
        for row in rows
        if row["entity_id"] == entity_id
        or _contains_entity_id(json.loads(row["payload"]), entity_id)
    ]
    if not event_ids:
        return
    placeholders = ",".join("?" for _ in event_ids)
    conn.execute(f"DELETE FROM events WHERE id IN ({placeholders})", tuple(event_ids))


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
