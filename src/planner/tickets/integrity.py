"""Keep invalid Ticket records local and readable without their Worker definition."""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from planner.conversation.contracts import ConversationSystem, require_conversation_backend_key
from planner.core.clock import Clock
from planner.core.contracts import ErrorCode, JsonDict, PlannerError
from planner.tickets.contracts import BrokenTicketProjection

# All displayed reasons are fixed structural descriptions. Neither exception text nor
# arbitrary detail values cross this boundary.
_STRUCTURAL_REASONS = {
    "unknown worker type": "PlannerError: unknown worker type (worker_type)",
    "stage outside the linear order": "PlannerError: stage outside the linear order (stage)",
    "ceiling outside the type's range": "PlannerError: ceiling outside the type's range (ceiling)",
    "ceiling holder does not exist": "PlannerError: ceiling holder does not exist (ceiling_holder)",
    "a Ticket cannot hold its own ceiling": (
        "PlannerError: a Ticket cannot hold its own ceiling (ceiling_holder)"
    ),
    "corrupt ticket field state": "PlannerError: corrupt ticket field state (fields or proposal)",
    "unknown agent backend": "PlannerError: unknown agent backend (employee_backend)",
}
_GENERIC_REASONS = {
    "PlannerError": "PlannerError: invalid stored Ticket structure",
    "JSONDecodeError": "JSONDecodeError: malformed stored JSON",
    "KeyError": "KeyError: missing stored Ticket member",
    "TypeError": "TypeError: invalid stored Ticket member type",
    "ValueError": "ValueError: invalid stored Ticket member value",
}
_FALLBACK_REASON = "TicketIntegrityError: structural fault reason unavailable"
_SAFE_REASONS = frozenset(
    (*_STRUCTURAL_REASONS.values(), *_GENERIC_REASONS.values(), _FALLBACK_REASON)
)


def safe_reason(cause: Exception | str) -> str:
    if isinstance(cause, str):
        return cause if cause in _SAFE_REASONS else _FALLBACK_REASON
    if isinstance(cause, PlannerError):
        return _STRUCTURAL_REASONS.get(cause.message, _GENERIC_REASONS["PlannerError"])
    if isinstance(cause, json.JSONDecodeError):
        return _GENERIC_REASONS["JSONDecodeError"]
    if isinstance(cause, KeyError):
        return _GENERIC_REASONS["KeyError"]
    if isinstance(cause, TypeError):
        return _GENERIC_REASONS["TypeError"]
    return _GENERIC_REASONS["ValueError"]


class TicketIntegrityError(PlannerError):
    def __init__(self, ticket_id: str, cause: Exception | str) -> None:
        reason = safe_reason(cause)
        super().__init__(
            ErrorCode.validation,
            "Ticket integrity failure",
            {"ticket_id": ticket_id, "reason": reason},
        )
        self.ticket_id = ticket_id
        self.reason = reason


def issue_for_ticket(conn: sqlite3.Connection, ticket_id: str) -> TicketIntegrityError | None:
    """Read the startup audit's nullable reason; never interpret human Ticket text."""
    # Historical migrations rebuild navigation before the new column exists.
    if "integrity_error_reason" not in {
        str(column[1]) for column in conn.execute("PRAGMA table_info(tickets)")
    }:
        return None
    row = conn.execute(
        "SELECT integrity_error_reason FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if row is None or row["integrity_error_reason"] is None:
        return None
    return TicketIntegrityError(ticket_id, str(row["integrity_error_reason"]))


def diagnostic_projection(
    conn: sqlite3.Connection,
    ticket_id: str,
    error: TicketIntegrityError | None = None,
) -> BrokenTicketProjection:
    # data imports the error/read helper; this raw projection reuses its row loader.
    from planner.tickets import data

    row = data._ticket_row(conn, ticket_id)
    issue = error or issue_for_ticket(conn, ticket_id)
    if issue is None:
        raise ValueError("Ticket has no integrity fault")
    result: JsonDict = {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "worker_type": str(row["worker_type"]),
        "stage": str(row["stage"]),
        "ticket_status": "errored",
        "integrity_error": {"reason": issue.reason},
        "recap": str(row["recap"]),
        "conversation_id": row["conversation_id"],
        "manager_id": row["manager_id"],
        "manager_kind": row["manager_kind"],
        "manager_title": row["manager_title"],
        "project_id": row["effective_project_id"],
        "project": row["project_name"],
        "is_backlogged": bool(row["is_backlogged"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return cast(BrokenTicketProjection, result)


def mark_faults(conn: sqlite3.Connection) -> list[TicketIntegrityError]:
    """Only startup validates and sets or clears the Ticket's dedicated error reason."""
    # data imports the error/read helper for write admission; the audit uses its loader.
    from planner.tickets import data

    observed: list[TicketIntegrityError] = []
    with data._txn(conn):
        for row in conn.execute(
            "SELECT id,integrity_error_reason FROM tickets ORDER BY id"
        ).fetchall():
            ticket_id = str(row["id"])
            issue: TicketIntegrityError | None
            try:
                ticket, _definition = data._load_ticket_and_worker_type_definition_for_write(
                    conn,
                    ticket_id,
                    startup_audit=True,
                )
                require_conversation_backend_key(ticket.employee_backend)
            except (PlannerError, KeyError, TypeError, ValueError) as error:
                issue = TicketIntegrityError(ticket_id, error)
                reason: str | None = issue.reason
            else:
                issue = None
                reason = None
            if row["integrity_error_reason"] == reason:
                continue
            conn.execute(
                "UPDATE tickets SET integrity_error_reason=? WHERE id=?", (reason, ticket_id)
            )
            if issue is not None:
                observed.append(issue)
    return observed


async def notify_chief(
    conn: sqlite3.Connection,
    conversations: ConversationSystem,
    clock: Clock,
    issues: list[TicketIntegrityError],
) -> None:
    """Use the ordinary system-message channel; a notice failure cannot stop startup."""
    import logging

    from planner.core.authctx import RequestContext
    from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL
    from planner.message_delivery import service

    for issue in issues:
        row = conn.execute("SELECT title FROM tickets WHERE id=?", (issue.ticket_id,)).fetchone()
        if row is None:
            continue
        try:
            await service.send_message(
                conversations,
                conn,
                clock,
                RequestContext(OWNER_PRINCIPAL),
                CHIEF_PRINCIPAL,
                f"The Ticket '{row['title']}' is unusable because its saved record is invalid. "
                f"Reason: {issue.reason}. Panels remains available. "
                "Inspect this Ticket before any repair.",
                expects_reply=False,
                system_message=True,
            )
        except Exception:
            logging.getLogger(__name__).exception("Could not tell Chief about a broken Ticket")
