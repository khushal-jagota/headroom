"""Move fixture Tickets through their real proposal/approval workflow."""

from sqlite3 import Connection

from planner.core.contracts import Principal, PrincipalKind
from planner.tickets import data
from planner.tickets.contracts import Ticket
from planner.worker_types.configuration import configured_worker_type_registry


def advance_ticket(
    conn: Connection, ticket_id: str, *, new_stage: str, principal: Principal, now: int
) -> Ticket:
    ticket = data.read_ticket(conn, ticket_id)
    definition = configured_worker_type_registry().require(ticket.worker_type)
    if definition.stage_index(new_stage) < definition.stage_index(ticket.stage):
        raise ValueError("fixture progression cannot rewind a Ticket")
    if ticket.pending_proposal is None:
        ticket = data.set_ceiling(conn, ticket_id, ceiling=new_stage, principal=principal, now=now)
    while ticket.stage != new_stage:
        if ticket.pending_proposal is not None:
            ticket = data.accept_proposal(
                conn,
                ticket_id,
                field=ticket.pending_proposal.field,
                principal=principal,
                now=now,
                next_ceiling=new_stage,
                next_holder=principal,
            )
        else:
            ticket = data.file_current_proposal_with_recap(
                conn,
                ticket_id,
                body="Fixture result",
                recap="Fixture progress",
                principal=Principal(PrincipalKind.ticket, ticket_id),
                now=now,
            )
    return ticket
