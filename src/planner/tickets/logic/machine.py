"""Ticket-specific state-machine rules over an explicit Worker-type definition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    Implementer,
    NextCeiling,
    ScopePair,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic import fields_codec
from planner.worker_types.contracts import WorkerTypeDefinition

if TYPE_CHECKING:
    from planner.tickets.contracts import Ticket


def field_is_passed(
    field: str,
    stage: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    """A field is passed when its gated Stage precedes the current Stage."""
    gated_stage = worker_type_definition.stage_gated_by(field)
    return worker_type_definition.stage_index(stage) > worker_type_definition.stage_index(
        gated_stage
    )


def auto_accept_target(
    stage: str,
    ceiling: str,
    field: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> str | None:
    if worker_type_definition.is_terminal(stage):
        return None
    if field != worker_type_definition.gating_field(stage):
        return None
    target = worker_type_definition.advance_target(stage)
    if target is None:
        raise PlannerError(ErrorCode.validation, "stage has no advance target", {"stage": stage})
    if worker_type_definition.stage_index(target) > worker_type_definition.stage_index(ceiling):
        return None
    return target


def at_or_beyond_ceiling(
    stage: str,
    ceiling: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    return worker_type_definition.stage_index(stage) >= worker_type_definition.stage_index(ceiling)


def resolve_scope(
    new_stage: str,
    next_ceiling: NextCeiling | None,
    at_cap: AtCap | None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> ScopePair:
    if next_ceiling is None or at_cap is None:
        missing: list[str] = []
        if next_ceiling is None:
            missing.append("next_ceiling")
        if at_cap is None:
            missing.append("at_cap")
        raise PlannerError(
            ErrorCode.scope_missing,
            "accept requires the onward scope pair",
            {"missing": missing},
        )
    if next_ceiling == NO_FURTHER:
        return ScopePair(next_ceiling=new_stage, at_cap=at_cap)
    ceiling_id = str(next_ceiling)
    if ceiling_id not in worker_type_definition.ceiling_range():
        raise PlannerError(
            ErrorCode.scope_invalid,
            "unknown next_ceiling",
            {"next_ceiling": ceiling_id},
        )
    if worker_type_definition.stage_index(ceiling_id) < worker_type_definition.stage_index(
        new_stage
    ):
        raise PlannerError(
            ErrorCode.scope_invalid,
            "next_ceiling must be at or beyond the new stage",
            {"next_ceiling": ceiling_id, "new_stage": new_stage},
        )
    return ScopePair(next_ceiling=ceiling_id, at_cap=at_cap)


def has_pending_gating_proposal(
    stage: str,
    fields: TicketFields,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    field = worker_type_definition.gating_field(stage)
    if field is None:
        return False
    return fields_codec.get_slot(fields, field).proposal is not None


def has_pending_parked_proposal(
    ticket: Ticket,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    return has_pending_gating_proposal(
        ticket.stage,
        ticket.fields,
        worker_type_definition=worker_type_definition,
    )


def plan_handoff_status(
    implementer: Implementer | None,
    old_stage: str,
    new_stage: str | None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> TicketStatus | None:
    if new_stage is None or implementer is None:
        return None
    effect = worker_type_definition.transition_effect(implementer.value, old_stage, new_stage)
    return TicketStatus(effect) if effect is not None else None
