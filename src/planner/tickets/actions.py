"""Ticket actions that coordinate one domain write with an external owner."""

from __future__ import annotations

import sqlite3

from planner.core.errors import ErrorCode, PlannerError
from planner.runtime.contracts import EmployeeRevisionRunner
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket
from planner.tickets.logic import admission


def return_ticket_for_revision(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str,
    actor: str,
    now: int,
    employee_revision_runner: EmployeeRevisionRunner | None,
) -> Ticket:
    """Accept a revision handoff before changing the canonical Ticket."""
    admission.validate_body(message, "revision guidance")
    if employee_revision_runner is None:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "employee runner is unavailable",
            {"ticket_id": ticket_id},
        )
    handoff = employee_revision_runner.reserve_revision(ticket_id, message.strip())
    try:
        ticket = tickets_data.return_for_revision(
            conn,
            ticket_id,
            message=message,
            actor=actor,
            now=now,
        )
    except BaseException:
        handoff.cancel()
        raise
    handoff.release()
    return ticket
