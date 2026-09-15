"""The durable outbox row written atomically with a parked proposal."""

from __future__ import annotations

import json
import sqlite3

from planner.core.contracts import Principal, PrincipalKind
from planner.proposal_holder_wakes.contracts import ProposalHolderWake, proposal_ready_message


def record_replacement(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    holder: Principal,
    message: str,
    now: int,
) -> None:
    """Replace the current delivery intent and earn a fresh proposal generation."""
    if holder.kind is PrincipalKind.owner:
        cancel(conn, ticket_id, now=now)
        return
    row = conn.execute(
        "SELECT proposal_generation FROM proposal_holder_wakes WHERE ticket_id = ?",
        (ticket_id,),
    ).fetchone()
    generation = 1 if row is None else int(row["proposal_generation"]) + 1
    conn.execute(
        "INSERT INTO proposal_holder_wakes "
        "(ticket_id,proposal_generation,delivery_attempt,holder_kind,holder_id,message,"
        "state,retry_at,last_error,created_at,updated_at,delivered_at) "
        "VALUES (?,?,1,?,?,?,'pending',?,NULL,?,?,NULL) "
        "ON CONFLICT(ticket_id) DO UPDATE SET "
        "proposal_generation=excluded.proposal_generation,delivery_attempt=1,"
        "holder_kind=excluded.holder_kind,holder_id=excluded.holder_id,message=excluded.message,"
        "state='pending',retry_at=excluded.retry_at,last_error=NULL,"
        "created_at=excluded.created_at,updated_at=excluded.updated_at,delivered_at=NULL",
        (ticket_id, generation, holder.kind.value, holder.id, message, now, now, now),
    )


def cancel(conn: sqlite3.Connection, ticket_id: str, *, now: int) -> None:
    conn.execute(
        "UPDATE proposal_holder_wakes SET state='cancelled',updated_at=? "
        "WHERE ticket_id=? AND state='pending'",
        (now, ticket_id),
    )


def due(
    conn: sqlite3.Connection, *, now: int, ticket_id: str | None = None
) -> tuple[ProposalHolderWake, ...]:
    ticket_filter = "" if ticket_id is None else " AND ticket_id = ?"
    parameters: tuple[object, ...] = (now,) if ticket_id is None else (now, ticket_id)
    rows = conn.execute(
        "SELECT ticket_id,proposal_generation,delivery_attempt,holder_kind,holder_id,message,"
        "retry_at FROM proposal_holder_wakes WHERE state='pending' AND retry_at <= ?"
        + ticket_filter
        + " "
        "ORDER BY retry_at,ticket_id",
        parameters,
    ).fetchall()
    return tuple(
        ProposalHolderWake(
            ticket_id=str(row["ticket_id"]),
            proposal_generation=int(row["proposal_generation"]),
            delivery_attempt=int(row["delivery_attempt"]),
            holder=Principal(PrincipalKind(str(row["holder_kind"])), str(row["holder_id"])),
            message=str(row["message"]),
            retry_at=int(row["retry_at"]),
        )
        for row in rows
    )


def reconcile_missing(conn: sqlite3.Connection, *, now: int) -> int:
    """Backfill a durable intent for a worker-authored proposal predating the outbox."""
    rows = conn.execute(
        "SELECT id,ceiling_holder FROM tickets WHERE pending_proposal IS NOT NULL "
        "AND json_extract(pending_proposal, '$.proposed_by') = 'worker' "
        "AND json_extract(ceiling_holder, '$.kind') != 'owner' "
        "AND id NOT IN (SELECT ticket_id FROM proposal_holder_wakes) ORDER BY id"
    ).fetchall()
    for row in rows:
        holder_raw = json.loads(str(row["ceiling_holder"]))
        holder = Principal(
            PrincipalKind(str(holder_raw["kind"])), str(holder_raw["id"])
        )
        ticket_id = str(row["id"])
        record_replacement(
            conn,
            ticket_id,
            holder=holder,
            message=proposal_ready_message(ticket_id),
            now=now,
        )
    return len(rows)


def mark_delivered(
    conn: sqlite3.Connection, wake: ProposalHolderWake, *, now: int
) -> bool:
    cursor = conn.execute(
        "UPDATE proposal_holder_wakes SET state='delivered',updated_at=?,delivered_at=? "
        "WHERE ticket_id=? AND proposal_generation=? AND delivery_attempt=? AND state='pending'",
        (
            now,
            now,
            wake.ticket_id,
            wake.proposal_generation,
            wake.delivery_attempt,
        ),
    )
    return cursor.rowcount == 1


def record_refusal(
    conn: sqlite3.Connection,
    wake: ProposalHolderWake,
    *,
    error: str,
    retry_at: int,
    now: int,
) -> bool:
    cursor = conn.execute(
        "UPDATE proposal_holder_wakes SET delivery_attempt=delivery_attempt+1,"
        "retry_at=?,last_error=?,updated_at=? WHERE ticket_id=? AND proposal_generation=? "
        "AND delivery_attempt=? AND state='pending'",
        (
            retry_at,
            error,
            now,
            wake.ticket_id,
            wake.proposal_generation,
            wake.delivery_attempt,
        ),
    )
    return cursor.rowcount == 1


def record_exception(
    conn: sqlite3.Connection,
    wake: ProposalHolderWake,
    *,
    error: str,
    retry_at: int,
    now: int,
) -> bool:
    cursor = conn.execute(
        "UPDATE proposal_holder_wakes SET retry_at=?,last_error=?,updated_at=? "
        "WHERE ticket_id=? AND proposal_generation=? AND delivery_attempt=? "
        "AND state='pending'",
        (
            retry_at,
            error,
            now,
            wake.ticket_id,
            wake.proposal_generation,
            wake.delivery_attempt,
        ),
    )
    return cursor.rowcount == 1
