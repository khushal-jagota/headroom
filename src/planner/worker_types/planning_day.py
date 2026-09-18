"""The complete behavior declaration for the ``planning-day`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

PLANNING_DAY_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="planning-day",
    label="Planning Day",
    stages=(
        StageDefinition(
            "needs_review", "Previous Day Review", "review", False, StageOwnershipMode.worker
        ),
        StageDefinition(
            "needs_direction",
            "Direction",
            "direction",
            False,
            StageOwnershipMode.paired,
        ),
        StageDefinition(
            "needs_day_changes",
            "Day Changes",
            "day_changes",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("review", "Previous Day Review"),
        FieldDefinition("direction", "Direction"),
        FieldDefinition("day_changes", "Day Changes"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-planning-day",
        default_model="opus[1m]",
        default_reasoning_effort="medium",
        toolset_profile="default",
        default_backend="claude",
    ),
)


__all__ = ["PLANNING_DAY_WORKER_TYPE_DEFINITION"]
