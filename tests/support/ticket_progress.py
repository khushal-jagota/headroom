"""Move fixture Tickets through their real proposal/approval workflow."""

from sqlite3 import Connection

from planner.tickets import data
from planner.tickets.contracts import AtCap, Ticket
from planner.worker_types.configuration import configured_worker_type_registry


def advance_ticket(
    conn: Connection, ticket_id: str, *, new_stage: str, actor: str, now: int
) -> Ticket:
    ticket = data.read_ticket(conn, ticket_id)
    definition = configured_worker_type_registry().require(ticket.worker_type)
    if definition.stage_index(new_stage) < definition.stage_index(ticket.stage):
        raise ValueError("fixture progression cannot rewind a Ticket")
    data.change_scope(
        conn, ticket_id, ceiling=new_stage, at_cap=AtCap.propose, actor=actor, now=now
    )
    while ticket.stage != new_stage:
        if ticket.pending_proposal is not None:
            ticket = data.accept_proposal(
                conn,
                ticket_id,
                field=ticket.pending_proposal.field,
                actor=actor,
                now=now,
                next_ceiling=new_stage,
                at_cap=AtCap.propose,
            )
        else:
            ticket = data.file_current_proposal_with_recap(
                conn,
                ticket_id,
                body="Fixture result",
                recap="Fixture progress",
                actor="agent",
                now=now,
            )
    return ticket
