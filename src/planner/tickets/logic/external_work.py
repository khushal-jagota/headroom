"""Pure reconciliation rules for work completed outside Panels."""

from __future__ import annotations

from collections.abc import Mapping

from planner.core.contracts import ErrorCode, EventKind, PlannerError
from planner.tickets.contracts import AtCap, FieldSlot, Ticket
from planner.tickets.logic import admission, fields_codec
from planner.tickets.logic.decisions import Decision, EventSpec
from planner.worker_types.contracts import WorkerTypeDefinition

CAUSE_EXTERNAL_WORK: str = "external_work"


def _prefix_count(worker_type_definition: WorkerTypeDefinition, target_stage: str) -> int:
    return worker_type_definition.stage_index(target_stage)


def decide_external_work(
    ticket: Ticket,
    target_stage: str,
    provided_values: Mapping[str, str],
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> tuple[Decision, Decision]:
    """Return the value and position decisions in their canonical event order."""
    if not worker_type_definition.supports_prefix_reconciliation:
        raise PlannerError(
            ErrorCode.validation,
            "type does not support external-work prefix reconciliation",
            {"worker_type": worker_type_definition.worker_type},
        )
    field_order = worker_type_definition.reconciliation_field_order()
    if target_stage not in worker_type_definition.ceiling_range():
        raise PlannerError(
            ErrorCode.validation,
            "external work target must be a linear ticket stage",
            {"stage": target_stage},
        )
    if ticket.stage == "dropped":
        raise PlannerError(ErrorCode.validation, "dropped is terminal")
    if worker_type_definition.stage_index(target_stage) < worker_type_definition.stage_index(
        ticket.stage
    ):
        raise PlannerError(
            ErrorCode.validation,
            "external work reconciliation cannot move backward",
            {"from_stage": ticket.stage, "to_stage": target_stage},
        )

    for field in field_order:
        if fields_codec.get_slot(ticket.fields, field).proposal is not None:
            raise PlannerError(
                ErrorCode.validation,
                "external work reconciliation rejects pending proposals",
                {"field": field},
            )

    expected_count = _prefix_count(worker_type_definition, target_stage)
    new_fields = ticket.fields
    value_events: list[EventSpec] = []
    for index, field in enumerate(field_order):
        slot = fields_codec.get_slot(ticket.fields, field)
        provided = provided_values.get(field)
        if provided is not None:
            admission.validate_body(provided, f"{field} value")
        if index < expected_count:
            final_value = provided if provided is not None else slot.value
            if final_value is None:
                raise PlannerError(
                    ErrorCode.validation,
                    "target stage requires a settled field prefix",
                    {"stage": target_stage, "field": field},
                )
            admission.validate_body(final_value, f"{field} value")
        else:
            if provided is not None or slot.value is not None:
                raise PlannerError(
                    ErrorCode.validation,
                    "target stage forbids settled values beyond its prefix",
                    {"stage": target_stage, "field": field},
                )
            final_value = None
        if final_value != slot.value:
            new_fields = fields_codec.with_slot(
                new_fields,
                field,
                FieldSlot(value=final_value, proposal=None, user_note=slot.user_note),
            )
            value_events.append(
                EventSpec(
                    EventKind.field_value_edited,
                    {"field": field, "body": final_value},
                )
            )

    position_events: list[EventSpec] = []
    new_stage: str | None = None
    if target_stage != ticket.stage:
        new_stage = target_stage
        position_events.append(
            EventSpec(
                EventKind.stage_changed,
                {
                    "from_stage": ticket.stage,
                    "to_stage": target_stage,
                    "cause": CAUSE_EXTERNAL_WORK,
                },
            )
        )
    scope_changes = ticket.ceiling != target_stage or ticket.at_cap != AtCap.stop
    if scope_changes:
        position_events.append(
            EventSpec(
                EventKind.scope_changed,
                {
                    "ceiling": target_stage,
                    "at_cap": AtCap.stop.value,
                    "cause": CAUSE_EXTERNAL_WORK,
                },
            )
        )

    return (
        Decision(events=tuple(value_events), new_fields=new_fields),
        Decision(
            events=tuple(position_events),
            new_stage=new_stage,
            new_ceiling=target_stage if scope_changes else None,
            new_at_cap=AtCap.stop if scope_changes else None,
        ),
    )
