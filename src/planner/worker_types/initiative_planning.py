"""The complete behavior declaration for the ``initiative_planning`` Worker type."""

from __future__ import annotations

from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)

INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="initiative_planning",
    label="Initiative Planning",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_rough_shape",
            "Rough Shape",
            "rough_shape",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition(
            "needs_question_tree",
            "Question Tree",
            "question_tree",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition(
            "needs_question_answers",
            "Question Answers",
            "question_answers",
            False,
            StageOwnershipMode.paired,
        ),
        StageDefinition(
            "needs_ticket_outlines",
            "Ticket Outlines",
            "ticket_outlines",
            False,
            StageOwnershipMode.worker,
        ),
        StageDefinition("needs_closeout", "Closeout", "closeout", False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition("rough_shape", "Rough Shape"),
        FieldDefinition("question_tree", "Question Tree"),
        FieldDefinition("question_answers", "Question Answers"),
        FieldDefinition("ticket_outlines", "Ticket Outlines"),
        FieldDefinition("closeout", "Closeout"),
    ),
    worker_profile=WorkerProfile(
        specialist_skill="panels-worker-initiative-planning",
        default_model="gpt-5.6-sol",
        default_reasoning_effort="medium",
        toolset_profile="default",
        default_backend="codex",
    ),
    supports_prefix_reconciliation=True,
)


__all__ = ["INITIATIVE_PLANNING_WORKER_TYPE_DEFINITION"]
