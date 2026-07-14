"""The complete behavior declaration for the ``coding`` Worker type."""

from __future__ import annotations

from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    TransitionHook,
    WorkerProfile,
    WorkerTypeDefinition,
)

CODING_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="coding",
    label="Coding",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False),
        StageDefinition("needs_success", "Success", "success", False),
        StageDefinition("needs_approach", "Approach", "approach", False),
        StageDefinition("needs_plan", "Plan", "plan", False),
        StageDefinition("needs_implementation", "Implementation", "implementation", False),
        StageDefinition("needs_closeout", "Closeout", "closeout", False),
        StageDefinition("done", "Done", None, True),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("success", "Success"),
        FieldDefinition("approach", "Approach"),
        FieldDefinition("plan", "Plan"),
        FieldDefinition("implementation", "Implementation"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-coding",
        model=None,
        reasoning_effort=None,
        toolset_profile="default",
    ),
    transition_hooks=(
        TransitionHook(
            old_stage="needs_plan",
            new_stage="needs_implementation",
            implementer="khushal",
            effect="user_takeover",
        ),
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["CODING_WORKER_TYPE_DEFINITION"]
