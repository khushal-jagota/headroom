"""The complete behavior declaration for the ``debugging`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

DEBUGGING_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="debugging",
    label="Debugging",
    stages=(
        StageDefinition(
            "needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker
        ),
        StageDefinition(
            "needs_problem_understanding",
            "Problem Understanding",
            "problem_understanding",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition(
            "needs_structural_diagnosis",
            "Structural Diagnosis",
            "structural_diagnosis",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition(
            "needs_solution", "Solution", "solution", False, StageOwnershipMode.worker
        ),
        StageDefinition(
            "needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker
        ),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("problem_understanding", "Problem Understanding"),
        FieldDefinition("structural_diagnosis", "Structural Diagnosis"),
        FieldDefinition("solution", "Solution"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-debugging",
        default_model="gpt-5.6-sol",
        default_reasoning_effort="high",
        toolset_profile="default",
        default_backend="codex",
    ),
)


__all__ = ["DEBUGGING_WORKER_TYPE_DEFINITION"]
