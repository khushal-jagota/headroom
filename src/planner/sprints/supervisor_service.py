"""The item-scoped application service for Sprint Item supervisors."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.core import authority
from planner.core.authctx import RequestContext
from planner.core.authority import require_above
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import Ticket

MAXIMUM_HISTORY_EVENTS = 100
# A worker step gets this long to prove it is alive before anyone may restart it.
WORKER_STEP_RESTART_FLOOR_SECONDS = 300


def _require_child_of(conn: sqlite3.Connection, sprint_item_id: str, ticket_id: str) -> None:
    """Refuse a request whose path names one Outcome and whose Ticket sits under another.

    The rule answers whether the caller may act on the Ticket. This answers whether the
    address is coherent, which is a separate question these routes ask because they carry
    the Outcome in the path. The addresses go in this Ticket's route step and this goes
    with them.
    """
    row = conn.execute(
        "SELECT 1 FROM tickets WHERE id = ? AND sprint_item_id = ?",
        (ticket_id, sprint_item_id),
    ).fetchone()
    if row is None:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "that Ticket is not a current child of that Outcome",
            {"sprint_item_id": sprint_item_id, "ticket_id": ticket_id},
        )


def require_current_child(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
) -> Ticket:
    """Return the child after the shared server-side authority check."""
    require_above(conn, ctx.principal, authority.ticket(ticket_id))
    _require_child_of(conn, sprint_item_id, ticket_id)
    return tickets_data.read_ticket(conn, ticket_id)


def ticket_context(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    now: int,
    triggering_message_sequence: int | None,
) -> dict[str, object]:
    with _coherent_read(conn):
        ticket = require_current_child(conn, ctx, sprint_item_id, ticket_id)
        triggering_message = None
        if triggering_message_sequence is not None and ticket.conversation_id is not None:
            row = conn.execute(
                "SELECT sequence, kind, payload, created_at FROM conversation_events "
                "WHERE conversation_id = ? AND kind = 'agent_message' AND sequence = ?",
                (ticket.conversation_id, triggering_message_sequence),
            ).fetchone()
            if row is not None:
                triggering_message = _event_json(row)
        if triggering_message_sequence is not None and triggering_message is None:
            raise PlannerError(
                ErrorCode.not_found,
                "the triggering Worker message is not in the current conversation",
                {
                    "ticket_id": ticket_id,
                    "conversation_id": ticket.conversation_id,
                    "sequence": triggering_message_sequence,
                },
            )
        day_ids = [
            str(row["day_id"])
            for row in conn.execute(
                "SELECT day_id FROM day_tickets WHERE ticket_id = ? ORDER BY day_id",
                (ticket_id,),
            ).fetchall()
        ]
        return {
            "sprint_item_id": sprint_item_id,
            "ticket": tickets_views.ticket_detail(conn, ticket_id, now),
            "day_ids": day_ids,
            "conversation_id": ticket.conversation_id,
            "triggering_worker_message": triggering_message,
        }


def conversation_history(
    conn: sqlite3.Connection,
    ctx: RequestContext,
    sprint_item_id: str,
    ticket_id: str,
    *,
    limit: int,
    before_sequence: int | None,
) -> dict[str, object]:
    if limit < 1 or limit > MAXIMUM_HISTORY_EVENTS:
        raise PlannerError(
            ErrorCode.validation,
            f"limit must be between 1 and {MAXIMUM_HISTORY_EVENTS}",
            {"limit": limit},
        )
    with _coherent_read(conn):
        ticket = require_current_child(conn, ctx, sprint_item_id, ticket_id)
        if ticket.conversation_id is None:
            raise PlannerError(
                ErrorCode.not_found,
                "the ticket has no current Worker conversation",
                {"ticket_id": ticket_id},
            )
        params: list[object] = [ticket.conversation_id]
        before_clause = ""
        if before_sequence is not None:
            before_clause = " AND sequence < ?"
            params.append(before_sequence)
        params.append(limit)
        rows = conn.execute(
            "SELECT * FROM ("
            "SELECT sequence, kind, payload, created_at FROM conversation_events "
            "WHERE conversation_id = ?"
            + before_clause
            + " ORDER BY sequence DESC LIMIT ?) ORDER BY sequence",
            tuple(params),
        ).fetchall()
        events = [_event_json(row) for row in rows]
        return {
            "sprint_item_id": sprint_item_id,
            "ticket_id": ticket_id,
            "conversation_id": ticket.conversation_id,
            "events": events,
            "has_more": bool(events and int(str(events[0]["sequence"])) > 1),
        }


@contextmanager
def _coherent_read(conn: sqlite3.Connection) -> Iterator[None]:
    """Hold one read result against child moves and conversation resets.

    A deferred ``BEGIN`` fixes one WAL snapshot at the scope check and does not reserve
    the writer. ``ROLLBACK`` closes an owned read on every exit without emitting the
    process change signal. Existing transaction owners keep ownership and supply their
    own coherent boundary.
    """
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if owns_transaction and conn.in_transaction:
            conn.execute("ROLLBACK")


def _event_json(row: sqlite3.Row) -> dict[str, object]:
    return {
        "sequence": int(row["sequence"]),
        "kind": str(row["kind"]),
        "payload": json.loads(str(row["payload"])),
        "created_at": int(row["created_at"]),
    }
