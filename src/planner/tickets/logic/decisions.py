"""The complete prospective Ticket state owned by resolution."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from planner.core.contracts import Principal
from planner.tickets.contracts import (
    PendingTicketProposal,
    Ticket,
    TicketFieldApprovals,
    TicketFieldValues,
)


@dataclass(frozen=True)
class Decision:
    field_values: TicketFieldValues
    field_approvals: TicketFieldApprovals
    pending_proposal: PendingTicketProposal | None
    stage: str
    ceiling: str
    ceiling_holder: Principal

    @classmethod
    def from_ticket(cls, ticket: Ticket) -> Decision:
        return cls(
            field_values=MappingProxyType(dict(ticket.field_values)),
            field_approvals=MappingProxyType(dict(ticket.field_approvals)),
            pending_proposal=ticket.pending_proposal,
            stage=ticket.stage,
            ceiling=ticket.ceiling,
            ceiling_holder=ticket.ceiling_holder,
        )
