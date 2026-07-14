"""The complete Automatic Employee-step eligibility decision."""

from __future__ import annotations

import sqlite3

from planner.core import links as core_links
from planner.tickets.contracts import AtCap, Ticket, TicketStatus
from planner.tickets.logic import machine
from planner.worker_types.contracts import WorkerTypeDefinition


def is_eligible_for_automatic_employee_step(
    conn: sqlite3.Connection,
    ticket: Ticket,
    *,
    planning_day_id: str,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    """Whether Planner may automatically start this Ticket's next Employee step now."""
    membership = conn.execute(
        "SELECT 1 FROM day_tickets WHERE day_id = ? AND ticket_id = ?",
        (planning_day_id, ticket.id),
    ).fetchone()
    if membership is None:
        return False
    if ticket.ticket_status is not TicketStatus.empty:
        return False
    if worker_type_definition.is_terminal(ticket.stage):
        return False
    if worker_type_definition.gating_field(ticket.stage) is None:
        return False
    if machine.has_pending_parked_proposal(
        ticket,
        worker_type_definition=worker_type_definition,
    ):
        return False
    if (
        machine.at_or_beyond_ceiling(
            ticket.stage,
            ticket.ceiling,
            worker_type_definition=worker_type_definition,
        )
        and ticket.at_cap is AtCap.stop
    ):
        return False
    if core_links.is_blocked(conn, ticket.id):
        return False
    return True
