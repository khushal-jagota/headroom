"""Stored state for one Ticket's current proposal-holder wake."""

from __future__ import annotations

from dataclasses import dataclass

from planner.core.contracts import Principal


def proposal_ready_message(ticket_id: str) -> str:
    """The wake omits proposal text because the holder reads canonical Ticket state."""
    return f"Ticket {ticket_id} has filed a proposal for your approval."


@dataclass(frozen=True, slots=True)
class ProposalHolderWake:
    ticket_id: str
    proposal_generation: int
    delivery_attempt: int
    holder: Principal
    message: str
    retry_at: int

    @property
    def sender_message_id(self) -> str:
        return (
            f"proposal-holder-wake:{self.ticket_id}:"
            f"{self.proposal_generation}:{self.delivery_attempt}"
        )
