"""Canonical storage operations for durable manager notices and delivery batches."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from uuid import uuid4

from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import commit_without_change_signal
from planner.manager_wakes.contracts import (
    ProposalNoticeState,
    ProposalNoticeSuppression,
    WakeBatch,
    WakeBatchStatus,
    WakeSourceKind,
)
from planner.manager_wakes.logic.proposal_notices import suppression_reason
from planner.measure_sources.contracts import FailureNoticeIdentity
from planner.measure_sources.notices import notice_is_current
from planner.tickets.logic.proposal_routing import parked_destination, route_to_holder


def create_wake(
    conn: sqlite3.Connection,
    *,
    manager_id: str | None,
    ticket_id: str | None,
    source_kind: WakeSourceKind,
    source_revision: int,
    summary: str,
    now: int,
    target: Principal | None = None,
    source_conversation_id: str | None = None,
    source_identity: FailureNoticeIdentity | None = None,
) -> bool:
    """Insert one wake for one persisted source identity. The caller owns the transaction."""
    if target is None and manager_id is None:
        raise ValueError("a notice requires an explicit recipient")
    resolved_target = target or Principal(PrincipalKind.manager, str(manager_id))
    if (source_kind is WakeSourceKind.source_failure) != (source_identity is not None):
        raise ValueError("source failure notice requires its typed identity")
    if source_identity is not None and source_identity.recipient != resolved_target:
        raise ValueError("source notice identity must match the recipient")
    inserted = conn.execute(
        "INSERT OR IGNORE INTO manager_wakes("
        "manager_id,ticket_id,source_kind,source_revision,summary,created_at,"
        "target_kind,target_id,source_conversation_id,measure_source_id,source_episode,"
        "source_responsibility_revision) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            manager_id,
            ticket_id,
            source_kind.value,
            source_revision,
            summary,
            now,
            resolved_target.kind.value,
            resolved_target.id,
            source_conversation_id,
            None if source_identity is None else source_identity.source_id,
            None if source_identity is None else source_identity.episode,
            None if source_identity is None else source_identity.responsibility_revision,
        ),
    )
    return inserted.rowcount == 1


def create_proposal_wake(
    conn: sqlite3.Connection,
    *,
    manager_id: str,
    ticket_id: str,
    proposal_revision: int,
    ticket_title: str,
    proposal_field: str,
    now: int,
    target: Principal | None = None,
) -> bool:
    return create_wake(
        conn,
        manager_id=manager_id,
        ticket_id=ticket_id,
        source_kind=WakeSourceKind.proposal,
        source_revision=proposal_revision,
        summary=(f"Ticket `{ticket_id}` ({ticket_title}) filed a proposal for `{proposal_field}`."),
        now=now,
        target=target,
    )


def create_worker_error_wake(
    conn: sqlite3.Connection,
    *,
    manager_id: str,
    ticket_id: str,
    claim_revision: int,
    ticket_title: str,
    now: int,
) -> bool:
    return create_wake(
        conn,
        manager_id=manager_id,
        ticket_id=ticket_id,
        source_kind=WakeSourceKind.worker_error,
        source_revision=claim_revision,
        summary=f"Ticket `{ticket_id}` ({ticket_title}) entered the explicit worker-error state.",
        now=now,
    )


def create_turn_failure_wake(
    conn: sqlite3.Connection,
    *,
    target: Principal,
    ticket_id: str,
    ticket_title: str,
    conversation_id: str,
    event_sequence: int,
    error_summary: str,
    now: int,
) -> bool:
    """Create the notice identified by the failed turn's durable event."""
    return create_wake(
        conn,
        manager_id=target.id if target.kind is PrincipalKind.manager else "",
        ticket_id=ticket_id,
        source_kind=WakeSourceKind.turn_failure,
        source_revision=event_sequence,
        source_conversation_id=conversation_id,
        target=target,
        summary=f"Ticket `{ticket_id}` ({ticket_title}) failed: {error_summary}",
        now=now,
    )


def _batch_from_row(row: sqlite3.Row) -> WakeBatch:
    target = Principal(PrincipalKind(str(row["target_kind"])), str(row["target_id"]))
    return WakeBatch(
        id=int(row["id"]),
        manager_id=(target.id if target.kind is PrincipalKind.manager else ""),
        target=target,
        sender_message_id=str(row["sender_message_id"]),
        message=str(row["message"]),
        status=WakeBatchStatus(str(row["status"])),
        conversation_id=(
            str(row["conversation_id"]) if row["conversation_id"] is not None else None
        ),
        process_token=(str(row["process_token"]) if row["process_token"] is not None else None),
    )


def batches_waiting_for_outcome(conn: sqlite3.Connection) -> tuple[WakeBatch, ...]:
    rows = conn.execute(
        "SELECT * FROM manager_wake_batches "
        "WHERE status IN ('offering','dispatching','accepted','uncertain') "
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


def release_pending_batch(conn: sqlite3.Connection, batch_id: int, *, process_token: str) -> bool:
    """Return a not-yet-sent batch to its source wakes so later events can join it."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        deleted = conn.execute(
            "DELETE FROM manager_wake_batches WHERE id=? AND status='pending' AND process_token=?",
            (batch_id, process_token),
        )
        commit_without_change_signal(conn)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return deleted.rowcount == 1


def preserve_interrupted_dispatches(
    conn: sqlite3.Connection, *, process_token: str, now: int
) -> int:
    """Keep a send interrupted by process loss unresolved instead of replaying it."""
    with conn:
        updated = conn.execute(
            "UPDATE manager_wake_batches SET status='uncertain',updated_at=? "
            "WHERE status IN ('offering','dispatching') "
            "AND (process_token IS NULL OR process_token != ?)",
            (now, process_token),
        )
    return updated.rowcount


def _compose_message(rows: Iterable[sqlite3.Row]) -> str:
    summaries = [str(row["summary"]) for row in rows]
    lines = "\n".join(f"- {summary}" for summary in summaries)
    return (
        "Review these new events that need your attention:\n\n"
        f"{lines}\n\n"
        "Read the canonical source or Ticket state before you act. These events can share one turn."
    )


def _obsolete_proposal(
    conn: sqlite3.Connection, row: sqlite3.Row
) -> ProposalNoticeSuppression | None:
    target = Principal(PrincipalKind(str(row["target_kind"])), str(row["target_id"]))
    ticket = conn.execute(
        "SELECT pending_proposal,pending_proposal_revision,ceiling_holder FROM tickets WHERE id=?",
        (row["ticket_id"],),
    ).fetchone()
    if ticket is None:
        current = ProposalNoticeState(False, None, None)
    else:
        holder = json.loads(str(ticket["ceiling_holder"]))
        reviewer = parked_destination(
            route_to_holder(Principal(PrincipalKind(holder["kind"]), holder["id"]))
        )
        current = ProposalNoticeState(
            True,
            int(ticket["pending_proposal_revision"])
            if ticket["pending_proposal"] is not None
            else None,
            reviewer,
        )
    reason = suppression_reason(
        source_revision=int(row["source_revision"]), target=target, current=current
    )
    if reason is None:
        return None
    return ProposalNoticeSuppression(
        int(row["id"]), row["ticket_id"], int(row["source_revision"]), target, current, reason
    )


def _retire_proposal(
    conn: sqlite3.Connection,
    suppression: ProposalNoticeSuppression,
    *,
    sender_message_id: str | None,
    now: int,
) -> None:
    current = suppression.current
    conn.execute(
        "INSERT OR IGNORE INTO manager_wake_suppressions("
        "wake_id,ticket_id,source_revision,target_kind,target_id,reason,ticket_exists,"
        "current_revision,current_target_kind,current_target_id,sender_message_id,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            suppression.wake_id,
            suppression.ticket_id,
            suppression.source_revision,
            suppression.target.kind.value,
            suppression.target.id,
            suppression.reason.value,
            int(current.ticket_exists),
            current.pending_revision,
            None if current.reviewer is None else current.reviewer.kind.value,
            None if current.reviewer is None else current.reviewer.id,
            sender_message_id,
            now,
        ),
    )
    conn.execute(
        "UPDATE manager_wakes SET closed_at=? WHERE id=? AND closed_at IS NULL",
        (now, suppression.wake_id),
    )


def admit_notice_batch(
    conn: sqlite3.Connection, *, conversation_id: str, sender_message_id: str | None, now: int
) -> tuple[bool, str | None]:
    """Check only this delivery owner's exact pre-wire candidate, in its transaction."""
    batch = conn.execute(
        "SELECT id,status FROM manager_wake_batches "
        "WHERE conversation_id=? AND sender_message_id=?",
        (conversation_id, sender_message_id),
    ).fetchone()
    if batch is None:
        return False, None
    # A durable terminal outcome cannot become a new send. Uncertain attempts are
    # historical evidence and never receive eligibility cleanup here.
    if batch["status"] in ("uncertain", "delivered", "refused"):
        raise RuntimeError("historical notice attempt cannot receive dispatch admission")
    if batch["status"] == "discarded":
        return True, "notice_batch_discarded"
    rows = conn.execute(
        "SELECT w.* FROM manager_wakes w JOIN manager_wake_batch_members m ON m.wake_id=w.id "
        "WHERE m.batch_id=? AND w.source_kind IN ('proposal','source_failure')",
        (batch["id"],),
    ).fetchall()
    stale_sources = tuple(
        row
        for row in rows
        if row["source_kind"] == "source_failure" and not notice_is_current(conn, row)
    )
    obsolete = tuple(
        value
        for row in rows
        if row["source_kind"] == "proposal" and (value := _obsolete_proposal(conn, row)) is not None
    )
    if obsolete or stale_sources:
        for row in stale_sources:
            conn.execute("UPDATE manager_wakes SET closed_at=? WHERE id=?", (now, row["id"]))
        for value in obsolete:
            _retire_proposal(conn, value, sender_message_id=sender_message_id, now=now)
        reasons = {value.reason.value for value in obsolete}
        if stale_sources:
            reasons.add("source_failure_changed")
        reason = ",".join(sorted(reasons))
        conn.execute(
            "UPDATE manager_wake_batches SET status='discarded',updated_at=? WHERE id=?",
            (now, batch["id"]),
        )
        return True, reason
    conn.execute(
        "UPDATE manager_wake_batches SET status='dispatching',updated_at=? WHERE id=? "
        "AND status IN ('pending','offering','accepted')",
        (now, batch["id"]),
    )
    return True, None


def _retire_obsolete_unsent_proposals(conn: sqlite3.Connection, *, now: int) -> None:
    rows = conn.execute(
        "SELECT w.* FROM manager_wakes w WHERE w.source_kind='proposal' AND w.closed_at IS NULL "
        "AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members m "
        "JOIN manager_wake_batches b ON b.id=m.batch_id WHERE m.wake_id=w.id "
        "AND b.status IN ('pending','offering','dispatching','accepted','uncertain'))"
    ).fetchall()
    for row in rows:
        obsolete = _obsolete_proposal(conn, row)
        if obsolete is not None:
            _retire_proposal(conn, obsolete, sender_message_id=None, now=now)


def _retire_obsolete_source_notices(conn: sqlite3.Connection, *, now: int) -> None:
    rows = conn.execute(
        "SELECT w.* FROM manager_wakes w WHERE w.source_kind='source_failure' "
        "AND w.closed_at IS NULL AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members m "
        "JOIN manager_wake_batches b ON b.id=m.batch_id WHERE m.wake_id=w.id "
        "AND b.status IN ('dispatching','accepted','uncertain'))"
    ).fetchall()
    for row in rows:
        if not notice_is_current(conn, row):
            conn.execute("UPDATE manager_wakes SET closed_at=? WHERE id=?", (now, row["id"]))


def claim_next_batch(conn: sqlite3.Connection, *, process_token: str, now: int) -> WakeBatch | None:
    """Group every currently available wake for the oldest waiting manager."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        _retire_obsolete_unsent_proposals(conn, now=now)
        _retire_obsolete_source_notices(conn, now=now)
        target = conn.execute(
            "SELECT w.target_kind,w.target_id FROM manager_wakes w "
            "WHERE w.closed_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM manager_wake_batch_members m "
            "JOIN manager_wake_batches b ON b.id=m.batch_id "
            "WHERE m.wake_id=w.id "
            "AND b.status IN ('pending','offering','dispatching','accepted','uncertain')) "
            "AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members recent_m "
            "JOIN manager_wake_batches recent_b ON recent_b.id=recent_m.batch_id "
            "WHERE recent_m.wake_id=w.id AND recent_b.status IN ('refused','discarded') "
            "AND recent_b.updated_at > ?) "
            "ORDER BY w.created_at,w.id LIMIT 1",
            (now - 5,),
        ).fetchone()
        if target is None:
            commit_without_change_signal(conn)
            return None
        target_kind = str(target["target_kind"])
        target_id = str(target["target_id"])
        manager_id = target_id if target_kind == "manager" else ""
        rows = conn.execute(
            "SELECT w.id,w.summary FROM manager_wakes w "
            "WHERE w.target_kind=? AND w.target_id=? "
            "AND w.closed_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM manager_wake_batch_members m "
            "JOIN manager_wake_batches b ON b.id=m.batch_id "
            "WHERE m.wake_id=w.id "
            "AND b.status IN ('pending','offering','dispatching','accepted','uncertain')) "
            "AND NOT EXISTS (SELECT 1 FROM manager_wake_batch_members recent_m "
            "JOIN manager_wake_batches recent_b ON recent_b.id=recent_m.batch_id "
            "WHERE recent_m.wake_id=w.id AND recent_b.status IN ('refused','discarded') "
            "AND recent_b.updated_at > ?) "
            "ORDER BY w.created_at,w.id",
            (target_kind, target_id, now - 5),
        ).fetchall()
        sender_message_id = f"manager_delivery_wake_{uuid4().hex}"
        inserted = conn.execute(
            "INSERT INTO manager_wake_batches("
            "manager_id,sender_message_id,message,status,process_token,created_at,updated_at,"
            "target_kind,target_id) VALUES (?,?,?,'pending',?,?,?,?,?)",
            (
                manager_id,
                sender_message_id,
                _compose_message(rows),
                process_token,
                now,
                now,
                target_kind,
                target_id,
            ),
        )
        if inserted.lastrowid is None:
            raise RuntimeError("manager wake batch insert returned no id")
        batch_id = inserted.lastrowid
        conn.executemany(
            "INSERT INTO manager_wake_batch_members(batch_id,wake_id) VALUES (?,?)",
            ((batch_id, int(row["id"])) for row in rows),
        )
        # Wake creation already announced the durable source. Batch construction is
        # internal queue bookkeeping, so announcing it would wake this loop itself.
        commit_without_change_signal(conn)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    row = conn.execute("SELECT * FROM manager_wake_batches WHERE id=?", (batch_id,)).fetchone()
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
            "process_token=?, updated_at=? WHERE id=? AND status='offering'",
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
            "AND status IN ('pending','offering','dispatching','accepted')",
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
            "WHERE id=? AND status IN ('pending','offering','dispatching','accepted')",
            (status.value, conversation_id, now, batch_id),
        )


def close_delivered_batch(conn: sqlite3.Connection, batch_id: int, *, now: int) -> None:
    """Close member wakes only after the batch's exact prompt row exists."""
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status='delivered',updated_at=? WHERE id=? "
            "AND status IN ('offering','dispatching','accepted','uncertain')",
            (now, batch_id),
        )
        conn.execute(
            "UPDATE manager_wakes SET closed_at=? WHERE closed_at IS NULL AND id IN ("
            "SELECT wake_id FROM manager_wake_batch_members WHERE batch_id=?)",
            (now, batch_id),
        )


def close_undeliverable_batch(conn: sqlite3.Connection, batch_id: int, *, now: int) -> None:
    """Close notices whose snapshotted recipient no longer exists."""
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status='discarded',updated_at=? WHERE id=?",
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


def record_batch_offering(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    conversation_id: str,
    process_token: str,
    now: int,
) -> None:
    with conn:
        conn.execute(
            "UPDATE manager_wake_batches SET status='offering',conversation_id=?,"
            "process_token=?,updated_at=? WHERE id=? AND status='pending'",
            (conversation_id, process_token, now, batch_id),
        )
