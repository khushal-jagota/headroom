"""The complete behavior declaration for the ``research`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

RESEARCH_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="research",
    label="Research",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_research_plan",
            "Research Plan",
            "research_plan",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_research", "Research", "research", False, StageOwnershipMode.worker),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("research_plan", "Research Plan"),
        FieldDefinition("research", "Research"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-research",
        default_model="opus[1m]",
        default_reasoning_effort="high",
        toolset_profile="default",
        default_backend="claude",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["RESEARCH_WORKER_TYPE_DEFINITION"]
