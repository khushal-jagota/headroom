"""Ticket-specific state-machine rules over an explicit Worker-type definition."""

from __future__ import annotations

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import (
    NO_FURTHER,
    NextCeiling,
    StageOwnershipMode,
    TicketStatus,
)
from planner.worker_types.contracts import WorkerTypeDefinition


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


def at_or_beyond_ceiling(
    stage: str,
    ceiling: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> bool:
    return worker_type_definition.stage_index(stage) >= worker_type_definition.stage_index(ceiling)


def resolve_next_ceiling(
    new_stage: str,
    next_ceiling: NextCeiling | None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> str:
    """The onward ceiling an approval grants, with the "none" sentinel concretized."""
    if next_ceiling is None:
        raise PlannerError(
            ErrorCode.scope_missing,
            "accept requires the onward ceiling",
            {"missing": ["next_ceiling"]},
        )
    if next_ceiling == NO_FURTHER:
        return new_stage
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
    return ceiling_id


def stage_ownership_mode(
    stage: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> StageOwnershipMode | None:
    if worker_type_definition.is_terminal(stage):
        return None
    ownership_mode = worker_type_definition.stage_definition(stage).ownership_mode
    if ownership_mode is None:
        raise PlannerError(
            ErrorCode.validation,
            "non-terminal stage has no ownership mode",
            {"stage": stage},
        )
    return ownership_mode


def resting_ticket_status(ownership_mode: StageOwnershipMode) -> TicketStatus:
    """All ownership modes enter at the one unclaimed control state."""
    return TicketStatus.empty


def worker_step_departure_status(ownership_mode: StageOwnershipMode) -> TicketStatus:
    """The status a Ticket occupies while its worker step is out.

    Every Stage rests at ``empty``. Worker-owned steps and the one collaborative opener
    for a user-owned Stage both occupy the ordinary ``agent`` control state.
    """
    if ownership_mode in {StageOwnershipMode.worker, StageOwnershipMode.user}:
        return TicketStatus.agent
    raise AssertionError(f"unknown ownership mode: {ownership_mode}")
