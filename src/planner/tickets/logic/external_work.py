"""Pure reconciliation rules for work completed outside Panels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from planner.core.contracts import ErrorCode, EventKind, PlannerError
from planner.tickets.contracts import (
    AtCap,
    CodingStage,
    FieldSlot,
    Ticket,
)
from planner.tickets.logic import admission, coding_bridge, fields_codec, machine
from planner.tickets.logic.decisions import Decision, EventSpec

if TYPE_CHECKING:
    from planner.tickets.logic.coding_bridge import WorkflowDefinition

CAUSE_EXTERNAL_WORK: str = "external_work"


def _gate_field_order(defn: WorkflowDefinition) -> tuple[str, ...]:
    """The settled-prefix field order: the gating field of each non-terminal stage,
    in linear stage order (kickoff-led). This is the AUTHORITATIVE reconciliation
    order — derived from the stages, NOT from ``field_ids`` (the registry does not
    order-align the declared field set with the stage order, so a type could legally
    declare them differently). For coding this is
    ``(kickoff, success, approach, plan, implementation, closeout)``."""
    return tuple(
        stage.gating_field
        for stage in defn.stages
        if not stage.is_terminal and stage.gating_field is not None
    )


def _prefix_count(defn: WorkflowDefinition, target_stage: str) -> int:
    """The number of settled fields a ``target_stage`` requires: its index in the
    linear stage order. ``stage_index`` counts ``needs_kickoff``=0, the first worker
    stage=1, … so ``stage_index(target)`` IS the settled-field count (kickoff through
    the field gating the stage before ``target``). Reproduces coding's map exactly
    (needs_success:1 … done:6)."""
    return coding_bridge.views.stage_index(defn, target_stage)


def decide_external_work(
    ticket: Ticket,
    target_stage: str,
    provided_values: Mapping[str, str],
    *,
    definition: WorkflowDefinition | None = None,
) -> tuple[Decision, Decision]:
    """Return value and position decisions in event order.

    The caller may place a recap event between the two decisions. Both decisions are
    built before any write, so validation failure cannot partially mutate a ticket.

    The reconciliation prefix is derived per-type from the definition's stages (the
    gating field of each non-terminal stage, in stage order), so any type whose
    definition declares ``supports_prefix_reconciliation`` reconciles through the
    same code. A type that declines prefix reconciliation is rejected loudly.
    """
    defn = definition or coding_bridge.coding_definition()
    if not defn.supports_prefix_reconciliation:
        raise PlannerError(
            ErrorCode.validation,
            "type does not support external-work prefix reconciliation",
            {"worker_type": defn.type_id},
        )
    field_order = _gate_field_order(defn)
    if str(target_stage) not in coding_bridge.views.ceiling_range(defn):
        raise PlannerError(
            ErrorCode.validation,
            "external work target must be a linear ticket stage",
            {"stage": str(target_stage)},
        )
    if ticket.stage == CodingStage.dropped:
        raise PlannerError(ErrorCode.validation, "dropped is terminal")
    if machine.stage_index(target_stage, definition=defn) < machine.stage_index(
        ticket.stage, definition=defn
    ):
        raise PlannerError(
            ErrorCode.validation,
            "external work reconciliation cannot move backward",
            {"from_stage": str(ticket.stage), "to_stage": str(target_stage)},
        )

    for field in field_order:
        slot = fields_codec.get_slot(ticket.fields, field)
        if slot.proposal is not None:
            raise PlannerError(
                ErrorCode.validation,
                "external work reconciliation rejects pending proposals",
                {"field": field},
            )

    expected_count = _prefix_count(defn, str(target_stage))
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
                    {"stage": str(target_stage), "field": field},
                )
            admission.validate_body(final_value, f"{field} value")
        else:
            if provided is not None or slot.value is not None:
                raise PlannerError(
                    ErrorCode.validation,
                    "target stage forbids settled values beyond its prefix",
                    {"stage": str(target_stage), "field": field},
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
                    {"field": field, "body": final_value},
                )
            )

    position_events: list[EventSpec] = []
    new_stage: str | None = None
    if str(target_stage) != str(ticket.stage):
        new_stage = str(target_stage)
        position_events.append(
            EventSpec(
                EventKind.stage_changed,
                {
                    "from_stage": str(ticket.stage),
                    "to_stage": str(target_stage),
                    "cause": CAUSE_EXTERNAL_WORK,
                },
            )
        )
    scope_changes = str(ticket.ceiling) != str(target_stage) or ticket.at_cap != AtCap.stop
    if scope_changes:
        position_events.append(
            EventSpec(
                EventKind.scope_changed,
                {
                    "ceiling": str(target_stage),
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
            new_ceiling=str(target_stage) if scope_changes else None,
            new_at_cap=AtCap.stop if scope_changes else None,
        ),
    )
