"""The complete behavior declaration for the ``exploration`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

EXPLORATION_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="exploration",
    label="Exploration",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_understanding",
            "Understanding",
            "understanding",
            False,
            StageOwnershipMode.paired,
        ),
        StageDefinition(
            "needs_research_plan",
            "Research Plan",
            "research_plan",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_research", "Research", "research", False, StageOwnershipMode.worker),
        StageDefinition("needs_answer", "Answer", "answer", False, StageOwnershipMode.paired),
        StageDefinition(
            "needs_follow_up",
            "Follow-up",
            "follow_up",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("understanding", "Understanding"),
        FieldDefinition("research_plan", "Research Plan"),
        FieldDefinition("research", "Research"),
        FieldDefinition("answer", "Answer"),
        FieldDefinition("follow_up", "Follow-up"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-exploration",
        model=None,
        reasoning_effort=None,
        toolset_profile="default",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["EXPLORATION_WORKER_TYPE_DEFINITION"]
