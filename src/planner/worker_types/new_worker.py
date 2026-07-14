"""The complete behavior declaration for the ``new_worker`` Worker type."""

from __future__ import annotations

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
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False),
        StageDefinition("needs_stages", "Stages", "stages", False),
        StageDefinition("needs_thinking", "Thinking", "thinking", False),
        StageDefinition("needs_drafting", "Drafting", "drafting", False),
        StageDefinition("needs_closeout", "Closeout", "closeout", False),
        StageDefinition("done", "Done", None, True),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
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
    transition_hooks=(),
    supports_prefix_reconciliation=True,
)


__all__ = ["NEW_WORKER_TYPE_DEFINITION"]
