"""State-machine math for §4.2/§4.3/§4.4: linear order indexing, gating and
advance tables, the auto-accept condition, ceiling comparison, and onward-scope
resolution. Pure: contracts + fields_codec only."""

from __future__ import annotations

from typing import Final

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import (
    ADVANCE_TARGET,
    GATING_FIELD,
    NO_FURTHER,
    STATE_ORDER,
    AtCap,
    FieldName,
    NextCeiling,
    ScopePair,
    TicketFields,
    TicketState,
)
from planner.tickets.logic import fields_codec

# Inverse of GATING_FIELD: the state each field gates. A field is "passed"
# once the ticket has advanced strictly beyond the state that field gates.
FIELD_GATES: Final[dict[FieldName, TicketState]] = {
    FieldName.success: TicketState.needs_success,
    FieldName.approach: TicketState.needs_approach,
    FieldName.plan: TicketState.needs_plan,
    FieldName.implementation: TicketState.needs_implementation,
    FieldName.closeout: TicketState.needs_closeout,
}


def state_index(state: TicketState) -> int:
    try:
        return STATE_ORDER.index(state)
    except ValueError as exc:
        raise PlannerError(
            ErrorCode.validation, "state outside the linear order", {"state": state.value}
        ) from exc


def is_terminal(state: TicketState) -> bool:
    return state in (TicketState.done, TicketState.dropped)


def gating_field(state: TicketState) -> FieldName | None:
    return GATING_FIELD.get(state)


def field_is_passed(field: FieldName, state: TicketState) -> bool:
    """A field is *passed* iff the state it gates strictly precedes the current
    state in STATE_ORDER. dropped is not in STATE_ORDER, so state_index(dropped)
    raises validation; the decide_edit_value check order rejects dropped explicitly
    before this is reached."""
    return state_index(state) > state_index(FIELD_GATES[field])


def advance_target(state: TicketState) -> TicketState:
    """The single linear step from a non-terminal state. Advancing never depends on
    the ceiling; the ceiling only decides whether a proposal auto-accepts."""
    target = ADVANCE_TARGET.get(state)
    if target is None:
        raise PlannerError(
            ErrorCode.validation, "state has no advance target", {"state": state.value}
        )
    return target


def auto_accept_target(
    state: TicketState, ceiling: TicketState, field: FieldName
) -> TicketState | None:
    if is_terminal(state):
        return None
    if field is not gating_field(state):
        return None
    target = advance_target(state)
    if state_index(target) > state_index(ceiling):
        return None
    return target


def at_or_beyond_ceiling(state: TicketState, ceiling: TicketState) -> bool:
    return state_index(state) >= state_index(ceiling)


def validate_ceiling(ceiling: TicketState) -> None:
    if ceiling not in STATE_ORDER:
        raise PlannerError(
            ErrorCode.scope_invalid, "ceiling must be a linear state", {"ceiling": ceiling.value}
        )


def resolve_scope(
    new_state: TicketState, next_ceiling: NextCeiling | None, at_cap: AtCap | None
) -> ScopePair:
    if next_ceiling is None or at_cap is None:
        missing: list[str] = []
        if next_ceiling is None:
            missing.append("next_ceiling")
        if at_cap is None:
            missing.append("at_cap")
        raise PlannerError(
            ErrorCode.scope_missing, "accept requires the onward scope pair", {"missing": missing}
        )
    if next_ceiling == NO_FURTHER:
        # the ceiling becomes exactly the newly entered state
        return ScopePair(next_ceiling=new_state, at_cap=at_cap)
    if not isinstance(next_ceiling, TicketState):
        raise PlannerError(
            ErrorCode.scope_invalid, "unknown next_ceiling", {"next_ceiling": str(next_ceiling)}
        )
    if next_ceiling not in STATE_ORDER or state_index(next_ceiling) < state_index(new_state):
        raise PlannerError(
            ErrorCode.scope_invalid,
            "next_ceiling must be at or beyond the new state",
            {"next_ceiling": next_ceiling.value, "new_state": new_state.value},
        )
    return ScopePair(next_ceiling=next_ceiling, at_cap=at_cap)


def has_pending_gating_proposal(state: TicketState, fields: TicketFields) -> bool:
    field = gating_field(state)
    if field is None:
        return False
    return fields_codec.get_slot(fields, field).proposal is not None
