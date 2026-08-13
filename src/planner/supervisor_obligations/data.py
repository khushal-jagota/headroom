"""Storage and reconciliation for Sprint Item supervisor obligations."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from uuid import uuid4

from planner.core.errors import ErrorCode, PlannerError
from planner.supervisor_obligations.contracts import (
    SupervisorDelivery,
    SupervisorObligation,
    SupervisorObligationKind,
    SupervisorObligationLifecycle,
)

OPEN_LIFECYCLES = ("pending", "delivered", "acknowledged", "failed")
MAXIMUM_PAGE = 100


def project_ticket(conn: sqlite3.Connection, ticket_id: str, now: int) -> None:
    """Project one Ticket inside its canonical writer transaction."""
    row = conn.execute(
        "SELECT id,sprint_item_id,stage,ticket_status,ticket_status_revision,backend_error,"
        "updated_at FROM tickets WHERE id=?",
        (ticket_id,),
    ).fetchone()
    if row is None or row["sprint_item_id"] is None:
        return
    for kind, source in _current_facts(row):
        conn.execute(
            "INSERT OR IGNORE INTO supervisor_obligations "
            "(id,sprint_item_id,ticket_id,kind,source_identity,lifecycle,attempt_count,"
            "created_at,updated_at) VALUES (?,?,?,?,?,'pending',0,?,?)",
            (f"obl_{uuid4().hex}", str(row["sprint_item_id"]), ticket_id, kind, source, now, now),
        )


def reconcile(conn: sqlite3.Connection, now: int) -> None:
    """Project current Ticket facts and close obligations whose source fact ended."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT id, sprint_item_id, stage, ticket_status, ticket_status_revision, "
            "backend_error, updated_at FROM tickets WHERE sprint_item_id IS NOT NULL"
        ).fetchall()
        live: set[tuple[str, str, str, str]] = set()
        for row in rows:
            ticket_id = str(row["id"])
            item_id = str(row["sprint_item_id"])
            for kind, source in _current_facts(row):
                live.add((ticket_id, item_id, kind, source))
                conn.execute(
                    "INSERT OR IGNORE INTO supervisor_obligations "
                    "(id,sprint_item_id,ticket_id,kind,source_identity,lifecycle,attempt_count,"
                    "created_at,updated_at) VALUES (?,?,?,?,?,'pending',0,?,?)",
                    (f"obl_{uuid4().hex}", item_id, ticket_id, kind, source, now, now),
                )
        open_rows = conn.execute(
            "SELECT id,ticket_id,sprint_item_id,kind,source_identity FROM "
            "supervisor_obligations WHERE lifecycle IN "
            "('pending','delivered','acknowledged','failed')"
        ).fetchall()
        for row in open_rows:
            expected = (str(row["sprint_item_id"]), str(row["kind"]), str(row["source_identity"]))
            if (str(row["ticket_id"]), *expected) in live:
                continue
            ticket_exists = conn.execute(
                "SELECT 1 FROM tickets WHERE id=?", (str(row["ticket_id"]),)
            ).fetchone()
            lifecycle = "resolved" if ticket_exists is not None else "superseded"
            conn.execute(
                "UPDATE supervisor_obligations SET lifecycle=?,delivery_id=NULL,updated_at=? "
                "WHERE id=?",
                (lifecycle, now, str(row["id"])),
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _current_facts(row: sqlite3.Row) -> tuple[tuple[str, str], ...]:
    status = str(row["ticket_status"])
    revision = int(row["ticket_status_revision"])
    ticket_id = str(row["id"])
    if status == "awaiting_agent_review":
        kind = SupervisorObligationKind.agent_review.value
    elif status == "awaiting_user_review":
        kind = SupervisorObligationKind.user_review.value
    elif status == "blocked":
        kind = SupervisorObligationKind.blocked.value
    elif status == "errored":
        kind = SupervisorObligationKind.worker_failure.value
    elif status == "needs_user":
        kind = SupervisorObligationKind.needs_user.value
    elif str(row["stage"]) == "done":
        kind = SupervisorObligationKind.completed.value
    else:
        kind = None
    if kind is None:
        return ()
    return ((kind, f"item:{row['sprint_item_id']}:ticket:{ticket_id}:status:{revision}:{status}"),)


def claim_batch(
    conn: sqlite3.Connection, now: int, *, maximum: int = 10
) -> tuple[SupervisorDelivery, tuple[SupervisorObligation, ...]] | None:
    """Claim the oldest bounded batch for one item in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        first = conn.execute(
            "SELECT sprint_item_id FROM supervisor_obligations WHERE lifecycle='pending' "
            "AND delivery_id IS NULL AND NOT EXISTS (SELECT 1 FROM supervisor_obligations older "
            "WHERE older.sprint_item_id=supervisor_obligations.sprint_item_id "
            "AND older.lifecycle='pending' AND (older.created_at<supervisor_obligations.created_at "
            "OR (older.created_at=supervisor_obligations.created_at "
            "AND older.id<supervisor_obligations.id))) "
            "AND (retry_at IS NULL OR retry_at<=?) ORDER BY created_at,id LIMIT 1",
            (now,),
        ).fetchone()
        if first is None:
            conn.execute("COMMIT")
            return None
        item_id = str(first[0])
        rows = conn.execute(
            "SELECT * FROM supervisor_obligations WHERE sprint_item_id=? "
            "AND lifecycle='pending' AND delivery_id IS NULL "
            "AND (retry_at IS NULL OR retry_at<=?) "
            "ORDER BY created_at,id LIMIT ?",
            (item_id, now, maximum),
        ).fetchall()
        delivery_id = f"supervisor_delivery_{uuid4().hex}"
        sender_message_id = delivery_id
        conn.execute(
            "INSERT INTO supervisor_obligation_deliveries "
            "(id,sprint_item_id,sender_message_id,state,created_at,updated_at) "
            "VALUES (?,?,?,'prepared',?,?)",
            (delivery_id, item_id, sender_message_id, now, now),
        )
        ids = tuple(str(row["id"]) for row in rows)
        conn.executemany(
            "INSERT INTO supervisor_obligation_delivery_members "
            "(delivery_id,obligation_id,ordinal) VALUES (?,?,?)",
            ((delivery_id, obligation_id, ordinal) for ordinal, obligation_id in enumerate(ids)),
        )
        conn.executemany(
            "UPDATE supervisor_obligations SET delivery_id=?,attempt_count=attempt_count+1,"
            "updated_at=? WHERE id=? AND lifecycle='pending'",
            ((delivery_id, now, obligation_id) for obligation_id in ids),
        )
        conn.execute("COMMIT")
        delivery = SupervisorDelivery(
            delivery_id, item_id, sender_message_id, None, "prepared", now, now
        )
        return delivery, tuple(_obligation(row) for row in rows)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def mark_dispatching(
    conn: sqlite3.Connection, delivery_id: str, conversation_id: str, now: int
) -> None:
    with conn:
        conn.execute(
            "UPDATE supervisor_obligation_deliveries SET state='dispatching',"
            "conversation_id=?,updated_at=? WHERE id=? AND state='prepared'",
            (conversation_id, now, delivery_id),
        )


def settle_delivery(
    conn: sqlite3.Connection,
    delivery_id: str,
    *,
    state: str,
    now: int,
    conversation_id: str | None = None,
    error: str | None = None,
    terminal: bool = False,
) -> None:
    lifecycle = "failed" if terminal else "delivered" if state == "delivered" else "pending"
    retry_at = None if lifecycle != "pending" else now + 5
    active_delivery_id = (
        delivery_id if state in {"queued", "uncertain", "failed", "delivered"} else None
    )
    with conn:
        conn.execute(
            "UPDATE supervisor_obligation_deliveries SET state=?,conversation_id=?,error=?,"
            "updated_at=? WHERE id=?",
            (state, conversation_id, error, now, delivery_id),
        )
        conn.execute(
            "UPDATE supervisor_obligations SET lifecycle=?,delivery_id=?,retry_at=?,last_error=?,"
            "updated_at=? WHERE delivery_id=?",
            (lifecycle, active_delivery_id, retry_at, error, now, delivery_id),
        )


def prepared_delivery(
    conn: sqlite3.Connection,
) -> tuple[SupervisorDelivery, tuple[SupervisorObligation, ...]] | None:
    row = conn.execute(
        "SELECT * FROM supervisor_obligation_deliveries WHERE state='prepared' "
        "ORDER BY created_at,id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    obligations = conn.execute(
        "SELECT o.* FROM supervisor_obligation_delivery_members m "
        "JOIN supervisor_obligations o ON o.id=m.obligation_id "
        "WHERE m.delivery_id=? AND o.lifecycle='pending' ORDER BY m.ordinal",
        (str(row["id"]),),
    ).fetchall()
    if not obligations:
        with conn:
            conn.execute(
                "UPDATE supervisor_obligation_deliveries SET state='failed',"
                "error='batch has no open obligations' WHERE id=?",
                (str(row["id"]),),
            )
        return None
    return (
        SupervisorDelivery(
            str(row["id"]),
            str(row["sprint_item_id"]),
            str(row["sender_message_id"]),
            None if row["conversation_id"] is None else str(row["conversation_id"]),
            str(row["state"]),
            int(row["created_at"]),
            int(row["updated_at"]),
        ),
        tuple(_obligation(obligation) for obligation in obligations),
    )


def mark_queued_outcome_uncertain(conn: sqlite3.Connection, delivery_id: str, now: int) -> None:
    settle_delivery(
        conn,
        delivery_id,
        state="uncertain",
        now=now,
        error="queued delivery left memory without a durable outcome",
        terminal=True,
    )


def reconcile_deliveries(conn: sqlite3.Connection, now: int) -> None:
    """Settle durable batches from conversation outcomes after sends or restarts."""
    rows = conn.execute(
        "SELECT d.id,d.sender_message_id,d.state FROM supervisor_obligation_deliveries d "
        "WHERE d.state IN ('dispatching','queued') ORDER BY d.created_at,d.id"
    ).fetchall()
    with conn:
        for row in rows:
            outcome = conn.execute(
                "SELECT kind FROM conversation_events WHERE "
                "json_extract(payload,'$.sender_message_id')=? "
                "AND kind IN ('prompt','prompt_delivery_refused','prompt_discarded') LIMIT 1",
                (str(row["sender_message_id"]),),
            ).fetchone()
            if outcome is not None and str(outcome["kind"]) == "prompt":
                settle_delivery(conn, str(row["id"]), state="delivered", now=now)
            elif outcome is not None:
                settle_delivery(
                    conn,
                    str(row["id"]),
                    state="refused",
                    now=now,
                    error=str(outcome["kind"]),
                )
            elif str(row["state"]) == "dispatching":
                # The backend write and prompt-event append are not atomic. Never repeat
                # an ambiguous prompt automatically.
                settle_delivery(
                    conn,
                    str(row["id"]),
                    state="uncertain",
                    now=now,
                    error="delivery outcome is uncertain after process interruption",
                    terminal=True,
                )


def list_for_item(
    conn: sqlite3.Connection, sprint_item_id: str, *, limit: int = 50, open_only: bool = True
) -> tuple[SupervisorObligation, ...]:
    if limit < 1 or limit > MAXIMUM_PAGE:
        raise PlannerError(
            ErrorCode.validation, f"limit must be between 1 and {MAXIMUM_PAGE}", {"limit": limit}
        )
    where = " AND lifecycle IN ('pending','delivered','acknowledged','failed')" if open_only else ""
    rows = conn.execute(
        "SELECT * FROM supervisor_obligations WHERE sprint_item_id=?"
        + where
        + " ORDER BY created_at,id LIMIT ?",
        (sprint_item_id, limit),
    ).fetchall()
    return tuple(_obligation(row) for row in rows)


def acknowledge(conn: sqlite3.Connection, sprint_item_id: str, ids: Sequence[str], now: int) -> int:
    if not ids:
        raise PlannerError(ErrorCode.validation, "obligation_ids must not be empty", {})
    placeholders = ",".join("?" for _ in ids)
    with conn:
        cursor = conn.execute(
            f"UPDATE supervisor_obligations SET lifecycle=CASE WHEN kind='completed' "
            f"THEN 'resolved' ELSE 'acknowledged' END,acknowledged_at=?,"
            f"updated_at=? WHERE sprint_item_id=? AND id IN ({placeholders}) "
            "AND lifecycle IN ('pending','delivered','failed')",
            (now, now, sprint_item_id, *ids),
        )
    return cursor.rowcount


def _obligation(row: sqlite3.Row) -> SupervisorObligation:
    return SupervisorObligation(
        id=str(row["id"]),
        sprint_item_id=str(row["sprint_item_id"]),
        ticket_id=str(row["ticket_id"]),
        kind=SupervisorObligationKind(str(row["kind"])),
        source_identity=str(row["source_identity"]),
        lifecycle=SupervisorObligationLifecycle(str(row["lifecycle"])),
        delivery_id=None if row["delivery_id"] is None else str(row["delivery_id"]),
        attempt_count=int(row["attempt_count"]),
        retry_at=None if row["retry_at"] is None else int(row["retry_at"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
        acknowledged_at=None if row["acknowledged_at"] is None else int(row["acknowledged_at"]),
    )
