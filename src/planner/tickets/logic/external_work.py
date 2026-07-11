"""Pure reconciliation rules for work completed outside Panels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from planner.core.contracts import ErrorCode, EventKind, PlannerError
from planner.tickets.contracts import (
    AtCap,
    FieldName,
    FieldSlot,
    Ticket,
    TicketState,
)
from planner.tickets.logic import admission, coding_bridge, fields_codec, machine
from planner.tickets.logic.decisions import Decision, EventSpec

if TYPE_CHECKING:
    from planner.tickets.logic.coding_bridge import WorkflowDefinition

CAUSE_EXTERNAL_WORK: str = "external_work"
_FIELD_ORDER = (
    FieldName.kickoff,
    FieldName.success,
    FieldName.approach,
    FieldName.plan,
    FieldName.implementation,
    FieldName.closeout,
)
_PREFIX_COUNT = {
    TicketState.needs_success: 1,
    TicketState.needs_approach: 2,
    TicketState.needs_plan: 3,
    TicketState.needs_implementation: 4,
    TicketState.needs_closeout: 5,
    TicketState.done: 6,
}


def decide_external_work(
    ticket: Ticket,
    target_state: TicketState,
    provided_values: Mapping[FieldName, str],
    *,
    definition: WorkflowDefinition | None = None,
) -> tuple[Decision, Decision]:
    """Return value and position decisions in event order.

    The caller may place a recap event between the two decisions. Both decisions are
    built before any write, so validation failure cannot partially mutate a ticket.

    External-work reconciliation is coding-only in t_tt02b: the reconciliation prefix
    (`_FIELD_ORDER`/`_PREFIX_COUNT`) is coding-bound, so a non-coding definition is
    rejected loudly here rather than silently reconciled with coding's prefix.
    Genericizing external-work is deferred to t_tt03.
    """
    defn = definition or coding_bridge.coding_definition()
    if defn.type_id != "coding":
        raise PlannerError(
            ErrorCode.validation,
            "external-work reconciliation is coding-only until t_tt03",
            {"type_id": defn.type_id},
        )
    if str(target_state) not in coding_bridge.views.ceiling_range(defn):
        raise PlannerError(
            ErrorCode.validation,
            "external work target must be a linear ticket state",
            {"state": target_state.value},
        )
    if ticket.state == TicketState.dropped:
        raise PlannerError(ErrorCode.validation, "dropped is terminal")
    if machine.state_index(target_state) < machine.state_index(ticket.state):
        raise PlannerError(
            ErrorCode.validation,
            "external work reconciliation cannot move backward",
            {"from": str(ticket.state), "to": target_state.value},
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
    if target_state != ticket.state:
        new_state = target_state
        position_events.append(
            EventSpec(
                EventKind.state_changed,
                {
                    "from": str(ticket.state),
                    "to": target_state.value,
                    "cause": CAUSE_EXTERNAL_WORK,
                },
            )
        )
    scope_changes = ticket.ceiling != target_state or ticket.at_cap != AtCap.stop
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
