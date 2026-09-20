"""Create, remove, and summarize relationships where one Ticket blocks another."""

from __future__ import annotations

import sqlite3
from collections import deque

from planner.core.contracts import (
    BlockedBySummaryRow,
    BlockedTicketSummaryRow,
    BlockerSummary,
)
from planner.core.errors import ErrorCode, PlannerError

# What makes a block live, as one SQL predicate. Every question about blocking asks it,
# under the alias ``blocker`` for the Ticket doing the blocking.
LIVE_BLOCKER_PREDICATE = "blocker.stage != 'done'"


def touch_blocked_ticket(conn: sqlite3.Connection, blocked_ticket_id: str, now: int) -> None:
    """A block arriving or leaving is activity on the Ticket it holds.

    Nothing about the blocked Ticket's own state is written: what it reads as is derived
    from the block rows. This moves only the activity time the Workspace rail orders by,
    so a Ticket that has just become blocked, or that its blocker has just freed, still
    rises where the user is looking.
    """
    conn.execute(
        "UPDATE tickets SET updated_at = ? WHERE id = ?",
        (now, blocked_ticket_id),
    )


def _ticket_is_active(conn: sqlite3.Connection, ticket_id: str) -> bool:
    row = conn.execute("SELECT stage FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    return row is not None and str(row["stage"]) != "done"


def _require_ticket(conn: sqlite3.Connection, ticket_id: str, field: str) -> None:
    if conn.execute("SELECT 1 FROM tickets WHERE id = ?", (ticket_id,)).fetchone() is None:
        raise PlannerError(
            ErrorCode.ticket_block_invalid,
            f"{field} must be an existing ticket",
            {field: ticket_id},
        )


def _active_blocked_ticket_ids(conn: sqlite3.Connection, ticket_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT ticket_blocks.blocked_ticket_id
        FROM ticket_blocks
        JOIN tickets blocker ON blocker.id = ticket_blocks.blocking_ticket_id
        WHERE ticket_blocks.blocking_ticket_id = ?
          AND """
        + LIVE_BLOCKER_PREDICATE
        + """
        ORDER BY ticket_blocks.blocked_ticket_id
        """,
        (ticket_id,),
    ).fetchall()
    return [str(row["blocked_ticket_id"]) for row in rows]


def would_create_active_ticket_block_cycle(
    conn: sqlite3.Connection, blocking_ticket_id: str, blocked_ticket_id: str
) -> bool:
    """Return true when a new active Ticket block closes an active cycle."""
    queue: deque[str] = deque([blocked_ticket_id])
    visited: set[str] = {blocked_ticket_id}
    while queue:
        ticket_id = queue.popleft()
        if ticket_id == blocking_ticket_id:
            return True
        for next_ticket_id in _active_blocked_ticket_ids(conn, ticket_id):
            if next_ticket_id not in visited:
                visited.add(next_ticket_id)
                queue.append(next_ticket_id)
    return False


def add_ticket_block(
    conn: sqlite3.Connection,
    blocking_ticket_id: str,
    blocked_ticket_id: str,
    now: int,
) -> None:
    """Record that one existing Ticket blocks another existing Ticket."""
    detail = {
        "blocking_ticket_id": blocking_ticket_id,
        "blocked_ticket_id": blocked_ticket_id,
    }
    if blocking_ticket_id == blocked_ticket_id:
        raise PlannerError(
            ErrorCode.ticket_block_invalid,
            "a ticket cannot block itself",
            detail,
        )
    own_txn = not conn.in_transaction
    if own_txn:
        conn.execute("BEGIN IMMEDIATE")
    try:
        _require_ticket(conn, blocking_ticket_id, "blocking_ticket_id")
        _require_ticket(conn, blocked_ticket_id, "blocked_ticket_id")
        if _ticket_is_active(conn, blocking_ticket_id) and would_create_active_ticket_block_cycle(
            conn, blocking_ticket_id, blocked_ticket_id
        ):
            raise PlannerError(
                ErrorCode.ticket_block_cycle,
                "ticket block would create an active cycle",
                detail,
            )
        try:
            conn.execute(
                "INSERT INTO ticket_blocks (blocking_ticket_id, blocked_ticket_id) VALUES (?, ?)",
                (blocking_ticket_id, blocked_ticket_id),
            )
        except sqlite3.IntegrityError as exc:
            raise PlannerError(
                ErrorCode.ticket_block_invalid,
                "ticket block already exists or violates a constraint",
                detail,
            ) from exc
        touch_blocked_ticket(conn, blocked_ticket_id, now)
    except BaseException:
        if own_txn:
            conn.execute("ROLLBACK")
        raise
    if own_txn:
        conn.execute("COMMIT")


def remove_ticket_block(
    conn: sqlite3.Connection,
    blocking_ticket_id: str,
    blocked_ticket_id: str,
    now: int,
) -> None:
    """Remove one Ticket block."""
    detail = {
        "blocking_ticket_id": blocking_ticket_id,
        "blocked_ticket_id": blocked_ticket_id,
    }
    cursor = conn.execute(
        "DELETE FROM ticket_blocks "
        "WHERE blocking_ticket_id = ? AND blocked_ticket_id = ?",
        (blocking_ticket_id, blocked_ticket_id),
    )
    if cursor.rowcount == 0:
        raise PlannerError(ErrorCode.not_found, "ticket block not found", detail)
    touch_blocked_ticket(conn, blocked_ticket_id, now)


def blocked_ticket_ids(conn: sqlite3.Connection) -> set[str]:
    """Return Tickets that an active Ticket blocks."""
    rows = conn.execute(
        "SELECT DISTINCT b.blocked_ticket_id FROM ticket_blocks b "
        "JOIN tickets blocker ON blocker.id = b.blocking_ticket_id "
        f"WHERE {LIVE_BLOCKER_PREDICATE}"
    ).fetchall()
    return {str(row["blocked_ticket_id"]) for row in rows}


def is_blocked(conn: sqlite3.Connection, ticket_id: str) -> bool:
    """Return whether an active Ticket blocks this Ticket."""
    return blocker_summary(conn, ticket_id).blocked


def blocker_summary(conn: sqlite3.Connection, ticket_id: str) -> BlockerSummary:
    """Return the direct incoming and outgoing Ticket blockers for one Ticket."""
    incoming_rows = conn.execute(
        """
        SELECT blocker.id, blocker.title, blocker.stage
        FROM ticket_blocks
        JOIN tickets blocker ON blocker.id = ticket_blocks.blocking_ticket_id
        WHERE ticket_blocks.blocked_ticket_id = ?
        ORDER BY """
        + LIVE_BLOCKER_PREDICATE
        + """ DESC,
          blocker.title COLLATE NOCASE,
          blocker.id
        """,
        (ticket_id,),
    ).fetchall()
    blocked_by = tuple(
        BlockedBySummaryRow(
            ticket_id=str(row["id"]),
            title=str(row["title"]),
            stage=str(row["stage"]),
            active=str(row["stage"]) != "done",
            href=f"#/workspace/{row['id']}",
        )
        for row in incoming_rows
    )

    source_active = _ticket_is_active(conn, ticket_id)
    outgoing_rows = conn.execute(
        """
        SELECT blocked.id, blocked.title
        FROM ticket_blocks
        JOIN tickets blocked ON blocked.id = ticket_blocks.blocked_ticket_id
        WHERE ticket_blocks.blocking_ticket_id = ?
        ORDER BY blocked.title COLLATE NOCASE, blocked.id
        """,
        (ticket_id,),
    ).fetchall()
    blocks = tuple(
        BlockedTicketSummaryRow(
            ticket_id=str(row["id"]),
            title=str(row["title"]),
            active=source_active,
            href=f"#/workspace/{row['id']}",
        )
        for row in outgoing_rows
    )

    return BlockerSummary(
        blocked=any(row.active for row in blocked_by),
        blocked_by=blocked_by,
        blocks=blocks,
    )
