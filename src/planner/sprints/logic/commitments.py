"""Pure checks for an exact carry-forward selection."""

from __future__ import annotations

from collections.abc import Sequence

from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import Ticket


def validate_carry_selection(
    source_sprint_id: str,
    target_sprint_id: str,
    outcome_id: str,
    tickets: Sequence[Ticket],
    terminal_ticket_ids: frozenset[str],
) -> None:
    if source_sprint_id == target_sprint_id:
        raise PlannerError(ErrorCode.validation, "carry requires a different Sprint")
    for ticket in tickets:
        if (
            ticket.sprint_item_id != outcome_id
            or ticket.sprint_id not in {source_sprint_id, target_sprint_id}
            or ticket.id in terminal_ticket_ids
        ):
            raise PlannerError(
                ErrorCode.validation,
                "carry selection is no longer eligible",
                {"ticket_id": ticket.id},
            )
