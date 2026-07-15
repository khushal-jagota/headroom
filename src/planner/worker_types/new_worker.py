"""The complete behavior declaration for the ``new_worker`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

NEW_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="new_worker",
    label="New Worker",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_understanding",
            "Understanding",
            "understanding",
            False,
            StageOwnershipMode.paired,
        ),
        StageDefinition("needs_stages", "Stages", "stages", False, StageOwnershipMode.worker),
        StageDefinition("needs_thinking", "Thinking", "thinking", False, StageOwnershipMode.worker),
        StageDefinition("needs_drafting", "Drafting", "drafting", False, StageOwnershipMode.worker),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("understanding", "Understanding"),
        FieldDefinition("stages", "Stages"),
        FieldDefinition("thinking", "Thinking"),
        FieldDefinition("drafting", "Drafting"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-new-worker",
        model=None,
        reasoning_effort=None,
        toolset_profile="default",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["NEW_WORKER_TYPE_DEFINITION"]
