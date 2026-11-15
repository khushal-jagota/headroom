"""The collections a thing can belong to, and the answer a membership write gives."""

from __future__ import annotations

from enum import StrEnum
from typing import TypedDict


class Collection(StrEnum):
    """Every collection Panels can put a thing into or take a thing out of."""

    manager_tickets = "manager_tickets"  # container: a Manager. member: a Ticket.

    @classmethod
    def _missing_(cls, value: object) -> Collection | None:
        if value == "outcome_tickets":
            return cls.manager_tickets
        return None

    blockers = "blockers"  # container: the blocked Ticket. member: the Ticket that blocks it.


class MembershipAnswer(TypedDict):
    """What both writes say: which membership this was, and that it now holds."""

    collection: str
    container_id: str
    member_id: str
    ok: bool
