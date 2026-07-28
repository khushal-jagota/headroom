"""The complete behavior declaration for the ``planning-midday-check`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

PLANNING_MIDDAY_CHECK_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="planning-midday-check",
    label="Planning Midday Check",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition("needs_action", "Action", "action", False, StageOwnershipMode.worker),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("action", "Action"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-planning-midday-check",
        default_model="gpt-5.6-terra",
        default_reasoning_effort="medium",
        toolset_profile="default",
        default_backend="codex",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["PLANNING_MIDDAY_CHECK_WORKER_TYPE_DEFINITION"]
