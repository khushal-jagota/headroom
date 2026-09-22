"""Canonical storage operations for Sprint Item manager wakes and delivery batches."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from uuid import uuid4

from planner.manager_wakes.contracts import WakeBatch, WakeBatchStatus, WakeSourceKind


def create_wake(
    conn: sqlite3.Connection,
    *,
    sprint_item_id: str,
    ticket_id: str,
    source_kind: WakeSourceKind,
    source_revision: int,
    summary: str,
    now: int,
) -> bool:
    """Insert one wake for one persisted source identity. The caller owns the transaction."""
    inserted = conn.execute(
        "INSERT OR IGNORE INTO manager_wakes("
        "sprint_item_id,ticket_id,source_kind,source_revision,summary,created_at) "
        "VALUES (?,?,?,?,?,?)",
        (sprint_item_id, ticket_id, source_kind.value, source_revision, summary, now),
    )
    return inserted.rowcount == 1


def create_proposal_wake(
    conn: sqlite3.Connection,
    *,
    sprint_item_id: str,
    ticket_id: str,
    proposal_revision: int,
    ticket_title: str,
    proposal_field: str,
    now: int,
) -> bool:
    return create_wake(
        conn,
        sprint_item_id=sprint_item_id,
        ticket_id=ticket_id,
        source_kind=WakeSourceKind.proposal,
        source_revision=proposal_revision,
        summary=(
            f"Ticket `{ticket_id}` ({ticket_title}) filed a proposal for `{proposal_field}`."
        ),
        now=now,
    )


def create_worker_error_wake(
    conn: sqlite3.Connection,
    *,
    sprint_item_id: str,
    ticket_id: str,
    claim_revision: int,
    ticket_title: str,
    now: int,
) -> bool:
    return create_wake(
        conn,
        sprint_item_id=sprint_item_id,
        ticket_id=ticket_id,
        source_kind=WakeSourceKind.worker_error,
        source_revision=claim_revision,
        summary=f"Ticket `{ticket_id}` ({ticket_title}) entered the explicit worker-error state.",
        now=now,
    )


def _batch_from_row(row: sqlite3.Row) -> WakeBatch:
    return WakeBatch(
        id=int(row["id"]),
        sprint_item_id=str(row["sprint_item_id"]),
        sender_message_id=str(row["sender_message_id"]),
        message=str(row["message"]),
        status=WakeBatchStatus(str(row["status"])),
        conversation_id=(
            str(row["conversation_id"]) if row["conversation_id"] is not None else None
        ),
        process_token=(
            str(row["process_token"]) if row["process_token"] is not None else None
        ),
    )


def batches_waiting_for_outcome(conn: sqlite3.Connection) -> tuple[WakeBatch, ...]:
    rows = conn.execute(
        "SELECT * FROM manager_wake_batches "
        "WHERE status IN ('dispatching','accepted','uncertain') "
        "ORDER BY id"
    ).fetchall()
    return tuple(_batch_from_row(row) for row in rows)


def recover_accepted_batches_from_other_processes(
    conn: sqlite3.Connection, *, process_token: str, now: int
) -> int:
    """Retry a known-held delivery after the process that held it ended."""
    with conn:
        updated = conn.execute(
            "UPDATE manager_wake_batches SET status='pending',process_token=NULL,"
            "conversation_id=NULL,updated_at=? "
            "WHERE status='accepted' "
            "AND (process_token IS NULL OR process_token != ?)",
            (now, process_token),
        )
    return updated.rowcount


def release_pending_batch(
    conn: sqlite3.Connection, batch_id: int, *, process_token: str
) -> bool:
    """Return a not-yet-sent batch to its source wakes so later events can join it."""
    with conn:
        deleted = conn.execute(
            "DELETE FROM manager_wake_batches WHERE id=? AND status='pending' "
            "AND process_token=?",
            (batch_id, process_token),
        )
    return deleted.rowcount == 1


def preserve_interrupted_dispatches(
    conn: sqlite3.Connection, *, process_token: str, now: int
) -> int:
    """Keep a send interrupted by process loss unresolved instead of replaying it."""
    with conn:
        updated = conn.execute(
            "UPDATE manager_wake_batches SET status='uncertain',updated_at=? "
            "WHERE status='dispatching' AND (process_token IS NULL OR process_token != ?)",
            (now, process_token),
        )
    return updated.rowcount


def _compose_message(rows: Iterable[sqlite3.Row]) -> str:
    summaries = [str(row["summary"]) for row in rows]
    lines = "\n".join(f"- {summary}" for summary in summaries)
    return (
        "Review these new events for your Sprint Item:\n\n"
        f"{lines}\n\n"
        "Read the canonical Ticket state before you act. These events can share one turn."
    )


def claim_next_batch(
    conn: sqlite3.Connection, *, process_token: str, now: int
) -> WakeBatch | None:
    """Group every currently available wake for the oldest waiting manager."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        target = conn.execute(
            "SELECT w.sprint_item_id FROM manager_wakes w "
            "WHERE w.closed_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM manager_wake_batch_members m "
            "JOIN manager_wake_batches b ON b.id=m.batch_id "
            "WHERE m.wake_id=w.id "
            "AND b.status IN ('pending','dispatching','accepted','uncertain')) "
            "AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members recent_m "
            "JOIN manager_wake_batches recent_b ON recent_b.id=recent_m.batch_id "
            "WHERE recent_m.wake_id=w.id AND recent_b.status IN ('refused','discarded') "
            "AND recent_b.updated_at > ?) "
            "ORDER BY w.created_at,w.id LIMIT 1",
            (now - 5,),
        ).fetchone()
        if target is None:
            conn.execute("COMMIT")
            return None
        sprint_item_id = str(target["sprint_item_id"])
        rows = conn.execute(
            "SELECT w.id,w.summary FROM manager_wakes w WHERE w.sprint_item_id=? "
            "AND w.closed_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM manager_wake_batch_members m "
            "JOIN manager_wake_batches b ON b.id=m.batch_id "
            "WHERE m.wake_id=w.id "
            "AND b.status IN ('pending','dispatching','accepted','uncertain')) "
            "AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members recent_m "
            "JOIN manager_wake_batches recent_b ON recent_b.id=recent_m.batch_id "
            "WHERE recent_m.wake_id=w.id AND recent_b.status IN ('refused','discarded') "
            "AND recent_b.updated_at > ?) "
            "ORDER BY w.created_at,w.id",
            (sprint_item_id, now - 5),
        ).fetchall()
        sender_message_id = f"supervisor_delivery_wake_{uuid4().hex}"
        inserted = conn.execute(
            "INSERT INTO manager_wake_batches("
            "sprint_item_id,sender_message_id,message,status,process_token,created_at,updated_at) "
            "VALUES (?,?,?,'pending',?,?,?)",
            (
                sprint_item_id,
                sender_message_id,
                _compose_message(rows),
                process_token,
                now,
                now,
            ),
        )
        if inserted.lastrowid is None:
            raise RuntimeError("manager wake batch insert returned no id")
        batch_id = inserted.lastrowid
        conn.executemany(
            "INSERT INTO manager_wake_batch_members(batch_id,wake_id) VALUES (?,?)",
            ((batch_id, int(row["id"])) for row in rows),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    row = conn.execute(
        "SELECT * FROM manager_wake_batches WHERE id=?", (batch_id,)
    ).fetchone()
    assert row is not None
    return _batch_from_row(row)


def pending_batches(conn: sqlite3.Connection) -> tuple[WakeBatch, ...]:
    rows = conn.execute(
        "SELECT * FROM manager_wake_batches WHERE status='pending' ORDER BY id"
    ).fetchall()
    return tuple(_batch_from_row(row) for row in rows)


def record_batch_accepted(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    conversation_id: str,
    process_token: str,
    now: int,
) -> None:
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status='accepted', conversation_id=?, "
            "process_token=?, updated_at=? WHERE id=? AND status='dispatching'",
            (conversation_id, process_token, now, batch_id),
        )


def record_batch_uncertain(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    conversation_id: str | None,
    process_token: str,
    now: int,
) -> None:
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status='uncertain', conversation_id=?, "
            "process_token=?, updated_at=? WHERE id=? "
            "AND status IN ('pending','dispatching','accepted')",
            (conversation_id, process_token, now, batch_id),
        )


def record_batch_failure(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    status: WakeBatchStatus,
    conversation_id: str | None,
    now: int,
) -> None:
    if status not in (WakeBatchStatus.refused, WakeBatchStatus.discarded):
        raise ValueError("a definite failure must be refused or discarded")
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status=?, conversation_id=?, updated_at=? "
            "WHERE id=? AND status IN ('pending','dispatching','accepted')",
            (status.value, conversation_id, now, batch_id),
        )


def close_delivered_batch(conn: sqlite3.Connection, batch_id: int, *, now: int) -> None:
    """Close member wakes only after the batch's exact prompt row exists."""
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status='delivered',updated_at=? WHERE id=? "
            "AND status IN ('dispatching','accepted','uncertain')",
            (now, batch_id),
        )
        conn.execute(
            "UPDATE manager_wakes SET closed_at=? WHERE closed_at IS NULL AND id IN ("
            "SELECT wake_id FROM manager_wake_batch_members WHERE batch_id=?)",
            (now, batch_id),
        )


def delivery_outcome_kind(
    conn: sqlite3.Connection, conversation_id: str, sender_message_id: str
) -> str | None:
    row = conn.execute(
        "SELECT kind FROM conversation_events WHERE conversation_id=? "
        "AND json_extract(payload,'$.sender_message_id')=? AND kind IN "
        "('prompt','prompt_delivery_refused','prompt_delivery_uncertain','prompt_discarded') "
        "ORDER BY sequence LIMIT 1",
        (conversation_id, sender_message_id),
    ).fetchone()
    return None if row is None else str(row["kind"])


def record_batch_dispatching(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    conversation_id: str,
    process_token: str,
    now: int,
) -> None:
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status='dispatching',conversation_id=?,"
            "process_token=?,updated_at=? WHERE id=? AND status='pending'",
            (conversation_id, process_token, now, batch_id),
        )
