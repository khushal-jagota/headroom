"""Keep invalid Ticket records local and readable without their Worker definition."""

from __future__ import annotations

import sqlite3
from typing import cast

from planner.conversation.contracts import ConversationSystem
from planner.core.clock import Clock
from planner.core.contracts import ErrorCode, JsonDict, PlannerError
from planner.tickets.contracts import BrokenTicketProjection


class TicketIntegrityError(PlannerError):
    def __init__(self, ticket_id: str, cause: Exception | str) -> None:
        reason = cause if isinstance(cause, str) else f"{type(cause).__name__}: {cause}"
        if isinstance(cause, PlannerError) and cause.detail:
            reason += f" ({cause.detail})"
        super().__init__(
            ErrorCode.validation,
            "Ticket integrity failure",
            {
                "ticket_id": ticket_id,
                "reason": reason,
            },
        )
        self.ticket_id = ticket_id
        self.reason = reason


def issue_for_ticket(conn: sqlite3.Connection, ticket_id: str) -> TicketIntegrityError | None:
    """Read only the startup audit's stored mark; this is not another validator."""
    row = conn.execute(
        "SELECT recap,worker_step_claim FROM tickets WHERE id=?", (ticket_id,)
    ).fetchone()
    if row is None or row["worker_step_claim"] != "errored":
        return None
    recap = str(row["recap"])
    if not recap.startswith(ERROR_PREFIX):
        return None
    reason = recap[len(ERROR_PREFIX) :].split(RECAP_SEPARATOR, 1)[0]
    return TicketIntegrityError(ticket_id, reason)


def diagnostic_projection(
    conn: sqlite3.Connection,
    ticket_id: str,
    error: TicketIntegrityError | None = None,
) -> BrokenTicketProjection:
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


ERROR_PREFIX = "Ticket integrity failure: "
RECAP_SEPARATOR = "\n\nPrevious recap:\n"


def mark_faults(conn: sqlite3.Connection) -> list[TicketIntegrityError]:
    """The startup audit records faults using the Ticket's existing error state."""
    from planner.tickets import data

    observed: list[TicketIntegrityError] = []
    with data._txn(conn):
        for row in conn.execute(
            "SELECT id,recap,worker_step_claim FROM tickets ORDER BY id"
        ).fetchall():
            ticket_id = str(row["id"])
            try:
                ticket, _definition = data._load_ticket_and_worker_type_definition_for_write(
                    conn,
                    ticket_id,
                    startup_audit=True,
                )
                data.require_conversation_backend_key(ticket.employee_backend)
            except (PlannerError, KeyError, TypeError, ValueError) as error:
                issue = TicketIntegrityError(ticket_id, error)
            else:
                continue
            recap = str(row["recap"])
            prefix = ERROR_PREFIX + issue.reason
            if (
                row["worker_step_claim"] == "errored"
                and recap.split(RECAP_SEPARATOR, 1)[0] == prefix
            ):
                continue
            if recap.startswith(ERROR_PREFIX):
                recap = recap.split(RECAP_SEPARATOR, 1)[1] if RECAP_SEPARATOR in recap else ""
            recorded = prefix + (RECAP_SEPARATOR + recap if recap else "")
            conn.execute(
                "UPDATE tickets SET worker_step_claim='errored',"
                "worker_step_claim_revision=worker_step_claim_revision+1,recap=? WHERE id=?",
                (recorded, ticket_id),
            )
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
