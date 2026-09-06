"""Readable preservation of a withdrawn, unapproved Ticket draft."""

from __future__ import annotations

import json
import re

from planner.tickets.contracts import PendingTicketProposal


def _identity(value: str) -> str:
    text = json.dumps(value, ensure_ascii=False)
    fence = "`" * (1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
    return f"{fence} {text} {fence}"


def archived_proposal(proposal: PendingTicketProposal) -> str:
    return (
        f"## Unapproved proposal\n\nField: {_identity(proposal.field)}\n\n"
        f"Author: {_identity(proposal.proposed_by)}\n\nCreated at: {proposal.created_at}\n\n"
        + proposal.body
    )
