"""Backward-only mappings against the definition that granted authority."""

from __future__ import annotations

from planner.core.contracts import ErrorCode, PlannerError
from planner.worker_types.contracts import WorkerTypeDefinition


def map_reference(
    reference: str,
    previous: WorkerTypeDefinition,
    replacement: WorkerTypeDefinition,
) -> str:
    if reference not in previous.stage_ids():
        raise PlannerError(
            ErrorCode.validation, "Ticket reference is outside its previous definition"
        )
    old_ids = previous.stage_ids()
    retained = tuple(stage for stage in old_ids if replacement.is_known_stage(stage))
    new_retained = tuple(stage for stage in replacement.stage_ids() if stage in old_ids)
    if retained != new_retained:
        raise PlannerError(
            ErrorCode.validation, "Stage reordering cannot preserve Ticket authority"
        )
    if replacement.is_known_stage(reference):
        return reference
    for candidate in reversed(old_ids[: previous.stage_index(reference)]):
        if replacement.is_known_stage(candidate) and not replacement.is_terminal(candidate):
            return candidate
    raise PlannerError(ErrorCode.validation, "removed reference has no earlier surviving Stage")
