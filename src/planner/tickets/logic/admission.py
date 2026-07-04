"""Write admission: who may write what, when. The §4.3 agent-proposal matrix,
the human-only gate (§7.6), recap/sprint/title/deadline/body validators (§3.3).
Pure: contracts/errors + machine only; raises PlannerError as the rejection."""

from __future__ import annotations

from datetime import date
from typing import Final

from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import AtCap, FieldName, TicketState
from planner.tickets.logic import machine

HUMAN_ACTOR: Final[str] = "human"


def is_human(actor: str) -> bool:
    return actor == HUMAN_ACTOR


def require_human(actor: str, action: str) -> None:
    if actor != HUMAN_ACTOR:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            f"{action} is a human-only action",
            {"action": action, "actor": actor},
        )


def check_agent_proposal(
    state: TicketState, ceiling: TicketState, at_cap: AtCap, field: FieldName
) -> None:
    if machine.is_terminal(state):
        raise PlannerError(
            ErrorCode.validation, "no proposals on a terminal ticket", {"state": state.value}
        )
    gating = machine.gating_field(state)
    if not machine.at_or_beyond_ceiling(state, ceiling):
        return
    if at_cap is AtCap.stop:
        raise PlannerError(
            ErrorCode.at_cap_stop,
            "ticket is at its ceiling with at_cap=stop",
            {
                "gating_field": gating.value if gating is not None else None,
                "state": state.value,
                "ceiling": ceiling.value,
                "at_cap": "stop",
            },
        )
    if field is not gating:
        raise PlannerError(
            ErrorCode.validation,
            "at the ceiling agents may propose only the current gating field",
            {
                "field": field.value,
                "gating_field": gating.value if gating is not None else None,
                "state": state.value,
            },
        )


def check_recap_writable(state: TicketState) -> None:
    if state is TicketState.needs_success or state is TicketState.dropped:
        raise PlannerError(
            ErrorCode.recap_too_early,
            "recap is writable only past needs_success",
            {"state": state.value},
        )


def check_sprint_assignable(ticket_id: str, sprint_item_id: str | None) -> None:
    if sprint_item_id is not None:
        raise PlannerError(
            ErrorCode.sprint_derived,
            "sprint_id is derived from the parent item",
            {"ticket_id": ticket_id, "sprint_item_id": sprint_item_id},
        )


def validate_title(title: str, max_chars: int) -> None:
    if not title:
        raise PlannerError(ErrorCode.validation, "title must be non-empty")
    if len(title) > max_chars:
        raise PlannerError(
            ErrorCode.title_too_long,
            f"title exceeds {max_chars} characters",
            {"length": len(title), "max": max_chars},
        )


def validate_deadline(deadline: str | None) -> None:
    if deadline is None:
        return
    try:
        date.fromisoformat(deadline)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation, "deadline must be an ISO date", {"deadline": deadline}
        ) from exc


def validate_body(body: str, what: str) -> None:
    if not body:
        raise PlannerError(ErrorCode.validation, f"{what} must be non-empty")
