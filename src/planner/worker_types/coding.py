"""The complete behavior declaration for the ``coding`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

CODING_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="coding",
    label="Coding",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition("needs_success", "Success", "success", False, StageOwnershipMode.worker),
        StageDefinition("needs_approach", "Approach", "approach", False, StageOwnershipMode.worker),
        StageDefinition("needs_plan", "Plan", "plan", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_implementation",
            "Implementation",
            "implementation",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
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
        default_employee_model=None,
        default_employee_reasoning_effort=None,
        toolset_profile="default",
        default_employee_backend="hermes",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["CODING_WORKER_TYPE_DEFINITION"]
