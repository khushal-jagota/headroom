"""Pure reconciliation rules for work completed outside Panels."""

from __future__ import annotations

from collections.abc import Mapping

from planner.core.contracts import ErrorCode, EventKind, PlannerError
from planner.tickets.contracts import (
    WORKER_STATE_ORDER,
    AtCap,
    FieldName,
    FieldSlot,
    Ticket,
    TicketState,
)
from planner.tickets.logic import admission, fields_codec, machine
from planner.tickets.logic.decisions import Decision, EventSpec

CAUSE_EXTERNAL_WORK: str = "external_work"
_FIELD_ORDER = (
    FieldName.success,
    FieldName.approach,
    FieldName.plan,
    FieldName.implementation,
    FieldName.closeout,
)
_PREFIX_COUNT = {
    TicketState.needs_success: 0,
    TicketState.needs_approach: 1,
    TicketState.needs_plan: 2,
    TicketState.needs_implementation: 3,
    TicketState.needs_closeout: 4,
    TicketState.done: 5,
}


def decide_external_work(
    ticket: Ticket,
    target_state: TicketState,
    provided_values: Mapping[FieldName, str],
) -> tuple[Decision, Decision]:
    """Return value and position decisions in event order.

    The caller may place a recap event between the two decisions. Both decisions are
    built before any write, so validation failure cannot partially mutate a ticket.
    """
    if target_state not in WORKER_STATE_ORDER:
        raise PlannerError(
            ErrorCode.validation,
            "external work target must be a linear ticket state",
            {"state": target_state.value},
        )
    if ticket.state is TicketState.dropped:
        raise PlannerError(ErrorCode.validation, "dropped is terminal")
    if machine.state_index(target_state) < machine.state_index(ticket.state):
        raise PlannerError(
            ErrorCode.validation,
            "external work reconciliation cannot move backward",
            {"from": ticket.state.value, "to": target_state.value},
        )

    for field in _FIELD_ORDER:
        slot = fields_codec.get_slot(ticket.fields, field)
        if slot.proposal is not None:
            raise PlannerError(
                ErrorCode.validation,
                "external work reconciliation rejects pending proposals",
                {"field": field.value},
            )

    expected_count = _PREFIX_COUNT[target_state]
    new_fields = ticket.fields
    value_events: list[EventSpec] = []
    for index, field in enumerate(_FIELD_ORDER):
        slot = fields_codec.get_slot(ticket.fields, field)
        provided = provided_values.get(field)
        if provided is not None:
            admission.validate_body(provided, f"{field.value} value")
        if index < expected_count:
            final_value = provided if provided is not None else slot.value
            if final_value is None:
                raise PlannerError(
                    ErrorCode.validation,
                    "target state requires a settled field prefix",
                    {"state": target_state.value, "field": field.value},
                )
            admission.validate_body(final_value, f"{field.value} value")
        else:
            if provided is not None or slot.value is not None:
                raise PlannerError(
                    ErrorCode.validation,
                    "target state forbids settled values beyond its prefix",
                    {"state": target_state.value, "field": field.value},
                )
            final_value = None
        if final_value != slot.value:
            new_slot = FieldSlot(
                value=final_value,
                proposal=None,
                user_note=slot.user_note,
            )
            new_fields = fields_codec.with_slot(new_fields, field, new_slot)
            value_events.append(
                EventSpec(
                    EventKind.field_value_edited,
                    {"field": field.value, "body": final_value},
                )
            )

    position_events: list[EventSpec] = []
    new_state: TicketState | None = None
    if target_state is not ticket.state:
        new_state = target_state
        position_events.append(
            EventSpec(
                EventKind.state_changed,
                {
                    "from": ticket.state.value,
                    "to": target_state.value,
                    "cause": CAUSE_EXTERNAL_WORK,
                },
            )
        )
    scope_changes = ticket.ceiling is not target_state or ticket.at_cap is not AtCap.stop
    if scope_changes:
        position_events.append(
            EventSpec(
                EventKind.scope_changed,
                {
                    "ceiling": target_state.value,
                    "at_cap": AtCap.stop.value,
                    "cause": CAUSE_EXTERNAL_WORK,
                },
            )
        )

    return (
        Decision(events=tuple(value_events), new_fields=new_fields),
        Decision(
            events=tuple(position_events),
            new_state=new_state,
            new_ceiling=target_state if scope_changes else None,
            new_at_cap=AtCap.stop if scope_changes else None,
        ),
    )
