"""The complete behavior declaration for the ``planning-sprint`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

PLANNING_SPRINT_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="planning-sprint",
    label="Planning Sprint",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition("needs_review", "Review", "review", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_next_sprint",
            "Next Sprint",
            "next_sprint",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("review", "Review"),
        FieldDefinition("next_sprint", "Next Sprint"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-planning-sprint",
        default_model="opus[1m]",
        default_reasoning_effort="medium",
        toolset_profile="default",
        default_backend="claude",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["PLANNING_SPRINT_WORKER_TYPE_DEFINITION"]
