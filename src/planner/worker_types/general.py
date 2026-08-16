"""The complete behavior declaration for the ``general`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

GENERAL_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="general",
    label="General",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_execution",
            "Execution",
            "execution",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("execution", "Execution"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-general",
        default_model="gpt-5.6-sol",
        default_reasoning_effort="medium",
        toolset_profile="default",
        default_backend="codex",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["GENERAL_WORKER_TYPE_DEFINITION"]
