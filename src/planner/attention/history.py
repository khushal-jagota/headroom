"""Durable approval events and owner gate intervals, in their source transaction."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict

from planner.attention.contracts import Destination
from planner.core.contracts import Principal


def _installed(conn: sqlite3.Connection) -> bool:
    # Historical migrations call today's notification capture before this schema exists.
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attention_history_metadata'"
        ).fetchone()
        is not None
    )


def _destination(conn: sqlite3.Connection, ticket_id: str) -> Destination | None:
    row = conn.execute(
        "SELECT t.id,t.title,CASE WHEN t.manager_kind='manager' THEN m.project_id "
        "ELSE t.project_id END AS project_id,t.manager_id "
        "FROM tickets t LEFT JOIN managers m ON m.id=t.manager_id WHERE t.id=?",
        (ticket_id,),
    ).fetchone()
    if row is None:
        return None
    return Destination(
        kind="ticket",
        id=str(row[0]),
        title=str(row[1]),
        url=f"/#/workspace/{ticket_id}",
        project_id=str(row[2]) if row[2] is not None else None,
        manager_id=str(row[3]) if row[3] is not None else None,
    )


def install_history(conn: sqlite3.Connection, *, started_at: int) -> None:
    """Install additive history without inventing pre-installation opening times."""
    conn.execute(
        "CREATE TABLE attention_history_metadata ("
        "singleton INTEGER PRIMARY KEY CHECK(singleton=1),started_at INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO attention_history_metadata VALUES (1,?)", (started_at,))
    conn.execute(
        "CREATE TABLE attention_approval_events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id TEXT NOT NULL,field TEXT NOT NULL,"
        "actor_kind TEXT NOT NULL,actor_id TEXT NOT NULL,method TEXT NOT NULL,"
        "occurred_at INTEGER NOT NULL,destination_json TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX attention_approval_events_time ON attention_approval_events(occurred_at)"
    )
    conn.execute(
        "CREATE TABLE attention_gate_intervals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,ticket_id TEXT NOT NULL,"
        "destination_json TEXT NOT NULL,opened_at INTEGER,observed_since INTEGER NOT NULL,"
        "closed_at INTEGER,CHECK(closed_at IS NULL OR closed_at>=observed_since))"
    )
    conn.execute(
        "CREATE UNIQUE INDEX attention_gate_intervals_open "
        "ON attention_gate_intervals(ticket_id) WHERE closed_at IS NULL"
    )
    for row in conn.execute(
        "SELECT subject_id FROM notification_attention_state "
        "WHERE subject_kind='ticket' AND notification_type='awaiting_approval' AND active=1"
    ).fetchall():
        destination = _destination(conn, str(row[0]))
        if destination is not None:
            conn.execute(
                "INSERT INTO attention_gate_intervals"
                "(ticket_id,destination_json,opened_at,observed_since) VALUES (?,?,NULL,?)",
                (destination.id, json.dumps(asdict(destination)), started_at),
            )


def record_approval(
    conn: sqlite3.Connection,
    *,
    ticket_id: str,
    field: str,
    principal: Principal,
    method: str,
    now: int,
) -> None:
    """Record one successful acceptance, including its actual actor and method."""
    if not _installed(conn):
        return
    destination = _destination(conn, ticket_id)
    if destination is None:
        return
    conn.execute(
        "INSERT INTO attention_approval_events"
        "(ticket_id,field,actor_kind,actor_id,method,occurred_at,destination_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            ticket_id,
            field,
            principal.kind.value,
            principal.id,
            method,
            now,
            json.dumps(asdict(destination)),
        ),
    )


def capture_gate(conn: sqlite3.Connection, *, ticket_id: str, active: bool, now: int) -> None:
    """Record owner approval gate edges without changing the caller's transaction."""
    if not _installed(conn):
        return
    opened = conn.execute(
        "SELECT id FROM attention_gate_intervals WHERE ticket_id=? AND closed_at IS NULL",
        (ticket_id,),
    ).fetchone()
    if not active:
        if opened is not None:
            conn.execute(
                "UPDATE attention_gate_intervals SET closed_at=? WHERE id=?",
                (now, int(opened[0])),
            )
        return
    if opened is not None:
        return
    destination = _destination(conn, ticket_id)
    if destination is None:
        return
    conn.execute(
        "INSERT INTO attention_gate_intervals"
        "(ticket_id,destination_json,opened_at,observed_since) VALUES (?,?,?,?)",
        (ticket_id, json.dumps(asdict(destination)), now, now),
    )
