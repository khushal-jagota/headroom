"""Persistent generation records; ended addresses never become active again."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal, cast

from planner.core.contracts import PlannerError
from planner.previews.contracts import (
    PreviewError,
    PreviewRecord,
    ProcessIdentity,
    TicketEligibility,
)
from planner.tickets.data import read_ticket
from planner.worker_types.store import read_definition

TABLE = "ticket_application_previews"


def eligibility(conn: sqlite3.Connection, ticket_id: str) -> TicketEligibility:
    try:
        ticket = read_ticket(conn, ticket_id)
        definition = read_definition(conn, ticket.worker_type)
    except PlannerError as exc:
        raise PreviewError("This Ticket no longer exists.", 410) from exc
    row = conn.execute(
        "SELECT folder_path FROM projects WHERE id=?", (ticket.project_id,)
    ).fetchone()
    folder = Path(row["folder_path"]) if row is not None and row["folder_path"] else None
    return TicketEligibility(ticket_id, folder, definition.is_terminal(ticket.stage), ticket.locale)


def from_row(row: sqlite3.Row) -> PreviewRecord:
    process = None
    if row["pid"] is not None:
        process = ProcessIdentity(
            str(row["boot_id"]),
            int(row["pid"]),
            int(row["start_ticks"]),
            int(row["listener_inode"]),
            int(row["port"]),
            str(row["worktree"]),
        )
    return PreviewRecord(
        str(row["ticket_id"]),
        str(row["generation"]),
        str(row["worktree"]),
        str(row["public_origin"]),
        str(row["server_epoch"]),
        int(row["created_at"]),
        cast(Literal["prepared", "active", "ended"], row["state"]),
        row["ended_reason"],
        process,
    )


def get(
    conn: sqlite3.Connection, ticket_id: str, generation: str | None = None
) -> PreviewRecord | None:
    if generation is not None:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE ticket_id=? AND generation=?", (ticket_id, generation)
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT * FROM {TABLE} WHERE ticket_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
    return from_row(row) if row else None


def live(conn: sqlite3.Connection) -> list[PreviewRecord]:
    return [from_row(row) for row in conn.execute(f"SELECT * FROM {TABLE} WHERE state != 'ended'")]


def end(conn: sqlite3.Connection, generation: str, reason: str) -> None:
    conn.execute(
        f"UPDATE {TABLE} SET state='ended', ended_reason=? WHERE generation=? AND state!='ended'",
        (reason, generation),
    )


def prepare(conn: sqlite3.Connection, record: PreviewRecord) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            f"UPDATE {TABLE} SET state='ended',ended_reason='Replaced by a new preparation.' "
            "WHERE ticket_id=? AND state!='ended'",
            (record.ticket_id,),
        )
        conn.execute(
            f"""INSERT INTO {TABLE}
                        (generation,ticket_id,worktree,public_origin,server_epoch,created_at,state)
                        VALUES(?,?,?,?,?,?,?)""",
            (
                record.generation,
                record.ticket_id,
                record.worktree,
                record.public_origin,
                record.server_epoch,
                record.created_at,
                record.state,
            ),
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def register(conn: sqlite3.Connection, record: PreviewRecord, process: ProcessIdentity) -> None:
    try:
        cursor = conn.execute(
            f"""UPDATE {TABLE} SET state='active',
                                boot_id=?,pid=?,start_ticks=?,listener_inode=?,port=?
                                WHERE generation=? AND state='prepared' AND server_epoch=?""",
            (
                process.boot_id,
                process.pid,
                process.start_ticks,
                process.listener_inode,
                process.port,
                record.generation,
                record.server_epoch,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise PreviewError("That listener is already registered to another Ticket.") from exc
    if cursor.rowcount != 1:
        raise PreviewError("This preparation has ended; prepare a new address.", 410)
