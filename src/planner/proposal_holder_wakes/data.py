"""The durable outbox row written atomically with a parked proposal."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.core.contracts import Principal, PrincipalKind
from planner.proposal_holder_wakes.contracts import (
    ProposalHolderWake,
    TicketRejectionMessage,
    proposal_ready_message,
)


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


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
        "WHERE ticket_id=? AND state != 'cancelled'",
        (now, ticket_id),
    )


def record_rejection_messages(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    lifecycle_message: str,
    comment: str,
    sender: Principal,
    now: int,
) -> None:
    """Record the ordered Worker messages in the caller's decision transaction."""
    lifecycle = lifecycle_message.strip()
    guidance = comment.strip()
    if not lifecycle or not guidance or not sender.id.strip():
        raise ValueError("durable rejection messages require non-empty content and sender")
    row = conn.execute(
        "SELECT COALESCE(MAX(rejection_generation),0) AS generation "
        "FROM ticket_rejection_messages WHERE ticket_id=?",
        (ticket_id,),
    ).fetchone()
    generation = int(row["generation"]) + 1
    values = (
        (1, lifecycle, None, None),
        (2, guidance, sender.kind.value, sender.id),
    )
    for sequence, message, sender_kind, sender_id in values:
        _insert_rejection_message(
            conn,
            ticket_id=ticket_id,
            generation=generation,
            sequence=sequence,
            message=message,
            sender_kind=sender_kind,
            sender_id=sender_id,
            now=now,
        )


def _insert_rejection_message(
    conn: sqlite3.Connection,
    *,
    ticket_id: str,
    generation: int,
    sequence: int,
    message: str,
    sender_kind: str | None,
    sender_id: str | None,
    now: int,
) -> None:
    """Insert one validated message. The decision transaction owns both calls."""
    message_id = f"{ticket_id}:{generation}:{sequence}"
    conn.execute(
        "INSERT INTO ticket_rejection_messages "
        "(id,ticket_id,rejection_generation,sequence,delivery_attempt,message,"
        "sender_kind,sender_id,state,retry_at,last_error,created_at,updated_at,delivered_at) "
        "VALUES (?,?,?,?,1,?,?,?,'pending',?,NULL,?,?,NULL)",
        (
            message_id,
            ticket_id,
            generation,
            sequence,
            message,
            sender_kind,
            sender_id,
            now,
            now,
            now,
        ),
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


def claim_due(
    conn: sqlite3.Connection,
    *,
    now: int,
    ticket_id: str | None = None,
) -> tuple[ProposalHolderWake, ...]:
    """Claim due rows before I/O so proposal writers serialize behind the send."""
    with _transaction(conn):
        candidates = list(due(conn, now=now, ticket_id=ticket_id))
        resumed: ProposalHolderWake | None = None
        if ticket_id is not None:
            row = conn.execute(
                "SELECT ticket_id,proposal_generation,delivery_attempt,holder_kind,holder_id,"
                "message,retry_at FROM proposal_holder_wakes WHERE ticket_id=? "
                "AND state='delivering'",
                (ticket_id,),
            ).fetchone()
            if row is not None:
                resumed = _wake_from_row(row)
        claimed: list[ProposalHolderWake] = []
        for wake in candidates:
            cursor = conn.execute(
                "UPDATE proposal_holder_wakes SET state='delivering',updated_at=? "
                "WHERE ticket_id=? AND proposal_generation=? AND delivery_attempt=? "
                "AND state='pending'",
                (now, wake.ticket_id, wake.proposal_generation, wake.delivery_attempt),
            )
            if cursor.rowcount == 1:
                claimed.append(wake)
        if resumed is not None:
            claimed.append(resumed)
        return tuple(claimed)


def recover_interrupted_deliveries(conn: sqlite3.Connection, *, now: int) -> int:
    """Return crash-abandoned claims to pending without changing their stable ID."""
    proposal_cursor = conn.execute(
        "UPDATE proposal_holder_wakes SET state='pending',retry_at=?,"
        "last_error='delivery interrupted by process restart',updated_at=? "
        "WHERE state='delivering'",
        (now, now),
    )
    rejection_cursor = conn.execute(
        "UPDATE ticket_rejection_messages SET state='pending',retry_at=?,"
        "last_error='delivery interrupted by process restart',updated_at=? "
        "WHERE state='delivering'",
        (now, now),
    )
    return proposal_cursor.rowcount + rejection_cursor.rowcount


def due_ticket_ids(conn: sqlite3.Connection, *, now: int) -> tuple[str, ...]:
    proposal_ids = {
        str(row["ticket_id"])
        for row in conn.execute(
            "SELECT ticket_id FROM proposal_holder_wakes "
            "WHERE state IN ('pending','delivering') AND retry_at<=? "
            "ORDER BY retry_at,ticket_id",
            (now,),
        ).fetchall()
    }
    rejection_ids = {
        str(row["ticket_id"])
        for row in conn.execute(
            "SELECT DISTINCT ticket_id FROM ticket_rejection_messages "
            "WHERE state IN ('pending','delivering') AND retry_at<=?",
            (now,),
        ).fetchall()
    }
    return tuple(sorted(proposal_ids | rejection_ids))


def has_delivering(conn: sqlite3.Connection) -> bool:
    """Return whether this process can still own a durable delivery claim."""
    return (
        conn.execute(
            "SELECT 1 FROM proposal_holder_wakes WHERE state='delivering' "
            "UNION ALL SELECT 1 FROM ticket_rejection_messages "
            "WHERE state='delivering' LIMIT 1"
        ).fetchone()
        is not None
    )


def claim_due_rejection_message(
    conn: sqlite3.Connection, *, ticket_id: str, now: int
) -> TicketRejectionMessage | None:
    """Claim the first unfinished message for one rejection in transcript order."""
    with _transaction(conn):
        row = conn.execute(
            "SELECT * FROM ticket_rejection_messages AS message "
            "WHERE ticket_id=? AND retry_at<=? AND state IN ('pending','delivering') "
            "AND NOT EXISTS (SELECT 1 FROM ticket_rejection_messages AS earlier "
            "WHERE earlier.ticket_id=message.ticket_id "
            "AND (earlier.rejection_generation < message.rejection_generation OR "
            "(earlier.rejection_generation=message.rejection_generation "
            "AND earlier.sequence < message.sequence)) "
            "AND earlier.state != 'delivered') "
            "ORDER BY rejection_generation,sequence LIMIT 1",
            (ticket_id, now),
        ).fetchone()
        if row is None:
            return None
        if str(row["state"]) == "pending":
            cursor = conn.execute(
                "UPDATE ticket_rejection_messages SET state='delivering',updated_at=? "
                "WHERE id=? AND state='pending'",
                (now, str(row["id"])),
            )
            if cursor.rowcount != 1:
                return None
        sender_kind = row["sender_kind"]
        sender = (
            None
            if sender_kind is None
            else Principal(PrincipalKind(str(sender_kind)), str(row["sender_id"]))
        )
        return TicketRejectionMessage(
            id=str(row["id"]),
            ticket_id=str(row["ticket_id"]),
            rejection_generation=int(row["rejection_generation"]),
            sequence=int(row["sequence"]),
            delivery_attempt=int(row["delivery_attempt"]),
            message=str(row["message"]),
            sender=sender,
            retry_at=int(row["retry_at"]),
        )


def settle_rejection_message(
    conn: sqlite3.Connection,
    message: TicketRejectionMessage,
    *,
    state: str,
    now: int,
    retry_at: int | None = None,
    error: str | None = None,
    advance_attempt: bool = False,
) -> bool:
    """Settle the exact claimed rejection message after one delivery outcome."""
    cursor = conn.execute(
        "UPDATE ticket_rejection_messages SET state=?,retry_at=?,last_error=?,updated_at=?,"
        "delivered_at=?,delivery_attempt=delivery_attempt+? "
        "WHERE id=? AND delivery_attempt=? AND state='delivering'",
        (
            state,
            message.retry_at if retry_at is None else retry_at,
            error,
            now,
            now if state == "delivered" else None,
            1 if advance_attempt else 0,
            message.id,
            message.delivery_attempt,
        ),
    )
    return cursor.rowcount == 1


def _wake_from_row(row: sqlite3.Row) -> ProposalHolderWake:
    return ProposalHolderWake(
        ticket_id=str(row["ticket_id"]),
        proposal_generation=int(row["proposal_generation"]),
        delivery_attempt=int(row["delivery_attempt"]),
        holder=Principal(PrincipalKind(str(row["holder_kind"])), str(row["holder_id"])),
        message=str(row["message"]),
        retry_at=int(row["retry_at"]),
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
        "WHERE ticket_id=? AND proposal_generation=? AND delivery_attempt=? AND state='delivering'",
        (
            now,
            now,
            wake.ticket_id,
            wake.proposal_generation,
            wake.delivery_attempt,
        ),
    )
    return cursor.rowcount == 1


def cancel_claimed(conn: sqlite3.Connection, wake: ProposalHolderWake, *, now: int) -> bool:
    """Cancel only the exact stale claim the delivery worker already owns."""
    cursor = conn.execute(
        "UPDATE proposal_holder_wakes SET state='cancelled',updated_at=? "
        "WHERE ticket_id=? AND proposal_generation=? AND delivery_attempt=? "
        "AND state='delivering'",
        (now, wake.ticket_id, wake.proposal_generation, wake.delivery_attempt),
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
        "UPDATE proposal_holder_wakes SET state='pending',delivery_attempt=delivery_attempt+1,"
        "retry_at=?,last_error=?,updated_at=? WHERE ticket_id=? AND proposal_generation=? "
        "AND delivery_attempt=? AND state='delivering'",
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
        "UPDATE proposal_holder_wakes SET state='pending',retry_at=?,last_error=?,updated_at=? "
        "WHERE ticket_id=? AND proposal_generation=? AND delivery_attempt=? "
        "AND state='delivering'",
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


def mark_uncertain(
    conn: sqlite3.Connection, wake: ProposalHolderWake, *, error: str, now: int
) -> bool:
    cursor = conn.execute(
        "UPDATE proposal_holder_wakes SET state='uncertain',last_error=?,updated_at=? "
        "WHERE ticket_id=? AND proposal_generation=? AND delivery_attempt=? "
        "AND state='delivering'",
        (error, now, wake.ticket_id, wake.proposal_generation, wake.delivery_attempt),
    )
    return cursor.rowcount == 1
