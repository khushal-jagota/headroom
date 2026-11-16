"""Move fixture Tickets through their real proposal/approval workflow."""

from sqlite3 import Connection

from planner.core.contracts import Principal, PrincipalKind
from planner.tickets import data
from planner.tickets.contracts import Ticket, TicketEdit


def advance_ticket(
    conn: Connection, ticket_id: str, *, new_stage: str, principal: Principal, now: int
) -> Ticket:
    ticket = data.read_ticket(conn, ticket_id)
    definition = ticket.workflow
    if definition.stage_index(new_stage) < definition.stage_index(ticket.stage):
        raise ValueError("fixture progression cannot rewind a Ticket")
    ceiling = (
        definition.stages[-2].id if definition.is_terminal(new_stage) else new_stage
    )
    if ticket.pending_proposal is None:
        ticket = data.edit_ticket(
            conn,
            ticket_id,
            edit=TicketEdit(ceiling=ceiling),
            title_max_chars=200,
            principal=principal,
            now=now,
        )
    while ticket.stage != new_stage:
        if ticket.pending_proposal is not None:
            ticket = data.accept_proposal(
                conn,
                ticket_id,
                field=ticket.pending_proposal.field,
                principal=principal,
                now=now,
                next_ceiling=(
                    None if definition.advance_target(ticket.stage) == new_stage
                    and definition.is_terminal(new_stage) else ceiling
                ),
                next_holder=principal,
            )
        else:
            ticket = data.file_current_proposal(
                conn,
                ticket_id,
                body="Fixture result",
                principal=Principal(PrincipalKind.ticket, ticket_id),
                now=now,
            )
    return ticket
