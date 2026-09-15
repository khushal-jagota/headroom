"""The complete prospective Ticket state owned by resolution."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from planner.core.contracts import Principal
from planner.tickets.contracts import AtCap, PendingTicketProposal, Ticket, TicketFieldValues


@dataclass(frozen=True)
class Decision:
    field_values: TicketFieldValues
    pending_proposal: PendingTicketProposal | None
    archived_field_content: str
    stage: str
    ceiling: str
    at_cap: AtCap
    ceiling_holder: Principal

    @classmethod
    def from_ticket(cls, ticket: Ticket) -> Decision:
        return cls(
            field_values=MappingProxyType(dict(ticket.field_values)),
            pending_proposal=ticket.pending_proposal,
            archived_field_content=ticket.archived_field_content,
            stage=ticket.stage,
            ceiling=ticket.ceiling,
            ceiling_holder=ticket.ceiling_holder,
            at_cap=ticket.at_cap,
        )
