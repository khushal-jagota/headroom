"""The complete behavior declaration for the ``amend_worker`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

AMEND_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="amend_worker",
    label="Amend Worker",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_amendment",
            "Amendment",
            "amendment",
            False,
            StageOwnershipMode.user,
        ),
        StageDefinition("needs_drafting", "Drafting", "drafting", False, StageOwnershipMode.worker),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("amendment", "Amendment"),
        FieldDefinition("drafting", "Drafting"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-amend-worker",
        default_model="gpt-5.6-sol",
        default_reasoning_effort="medium",
        toolset_profile="default",
        default_backend="codex",
    ),
)


__all__ = ["AMEND_WORKER_TYPE_DEFINITION"]
