"""The collections a thing can belong to, and the answer a membership write gives."""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class Collection(StrEnum):
    """Every collection Panels can put a thing into or take a thing out of."""

    day_tickets = "day_tickets"  # container: a Day, by date. member: a Ticket.
    outcome_tickets = "outcome_tickets"  # container: an Outcome. member: a Ticket.
    sprint_outcomes = "sprint_outcomes"  # container: a Sprint. member: an Outcome.
    blockers = "blockers"  # container: the blocked Ticket. member: the Ticket that blocks it.


class MembershipAnswer(TypedDict):
    """What both writes say: which membership this was, and that it now holds."""

    collection: str
    container_id: str
    member_id: str
    ok: bool
