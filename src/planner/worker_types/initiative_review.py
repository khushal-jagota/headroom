"""The complete behavior declaration for the ``initiative_review`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

INITIATIVE_REVIEW_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="initiative_review",
    label="Initiative Review",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition("needs_review", "Review", "review", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_feedback",
            "Feedback",
            "feedback",
            False,
            StageOwnershipMode.user,
        ),
        StageDefinition(
            "needs_followups",
            "Follow-ups",
            "followups",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("review", "Review"),
        FieldDefinition("feedback", "Feedback"),
        FieldDefinition("followups", "Follow-ups"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-initiative-review",
        default_model="gpt-6-astra",
        default_reasoning_effort="high",
        toolset_profile="default",
        default_backend="codex",
    ),
)


__all__ = ["INITIATIVE_REVIEW_WORKER_TYPE_DEFINITION"]
