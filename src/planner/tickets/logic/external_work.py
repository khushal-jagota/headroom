"""Pure reconciliation rules for work completed outside Panels."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import Ticket
from planner.tickets.logic import admission
from planner.tickets.logic.decisions import Decision
from planner.worker_types.contracts import WorkerTypeDefinition


def _prefix_count(worker_type_definition: WorkerTypeDefinition, target_stage: str) -> int:
    return worker_type_definition.stage_index(target_stage)


def decide_external_work(
    ticket: Ticket,
    target_stage: str,
    provided_values: Mapping[str, str],
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    """Validate the exact saved prefix and return its complete prospective state."""
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

    if ticket.pending_proposal is not None:
        raise PlannerError(
            ErrorCode.validation, "external work reconciliation rejects pending proposals"
        )

    expected_count = _prefix_count(worker_type_definition, target_stage)
    new_fields = dict(ticket.field_values)
    for index, field in enumerate(field_order):
        value = ticket.field_values.get(field)
        provided = provided_values.get(field)
        if provided is not None:
            admission.validate_body(provided, f"{field} value")
        if index < expected_count:
            final_value = provided if provided is not None else value
            if final_value is None:
                raise PlannerError(
                    ErrorCode.validation,
                    "target stage requires a settled field prefix",
                    {"stage": target_stage, "field": field},
                )
            admission.validate_body(final_value, f"{field} value")
        else:
            if provided is not None or value is not None:
                raise PlannerError(
                    ErrorCode.validation,
                    "target stage forbids settled values beyond its prefix",
                    {"stage": target_stage, "field": field},
                )
            final_value = None
        if final_value is not None:
            new_fields[field] = final_value
        else:
            new_fields.pop(field, None)
    return replace(
        Decision.from_ticket(ticket),
        field_values=new_fields,
        stage=target_stage,
        ceiling=target_stage,
    )
