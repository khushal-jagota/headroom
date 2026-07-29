"""The complete behavior declaration for the ``personal`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

PERSONAL_TASK_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="personal",
    label="Personal Task",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.user),
        StageDefinition("needs_outcome", "Outcome", "outcome", False, StageOwnershipMode.user),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("outcome", "Outcome"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-personal-task",
        default_model="gpt-5.6-luna",
        default_reasoning_effort="medium",
        toolset_profile="default",
        default_backend="codex",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["PERSONAL_TASK_WORKER_TYPE_DEFINITION"]
