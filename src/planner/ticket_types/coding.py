"""The ``coding`` WorkflowDefinition: the first shipped Worker type, reproducing
today's landed lifecycle EXACTLY.

Stages, gates, and fields are sourced by walking the ``tickets/contracts`` leaf
enums (CODING_STAGE_ORDER, CODING_GATING_FIELD_BY_STAGE, FieldName) so the golden
tests assert equality against the live constants rather than a hand-copied order. The worker profile
and the transition hook are DECLARED but INERT this ticket — nothing consumes
them, and ``plan_handoff_status`` is not changed.

Adding a second production type is a new file plus one line appended to the
build_registry call at the composition root."""

from __future__ import annotations

from planner.ticket_types.contracts import (
    FieldDef,
    Stage,
    TransitionHook,
    WorkerProfile,
    WorkflowDefinition,
)
from planner.tickets.contracts import (
    CODING_GATING_FIELD_BY_STAGE,
    CODING_STAGE_ORDER,
    CodingStage,
    FieldName,
    Implementer,
    TicketStatus,
)

# Display labels: underscore-stripped, title-cased stage/field names. ui.ts derives
# these at render time (labelize); there are no canonical hardcoded label strings
# to reuse, so these stand as the single label source (BRIEF decision 3).
_STAGE_LABELS: dict[str, str] = {
    CodingStage.needs_kickoff.value: "Kickoff",
    CodingStage.needs_success.value: "Success",
    CodingStage.needs_approach.value: "Approach",
    CodingStage.needs_plan.value: "Plan",
    CodingStage.needs_implementation.value: "Implementation",
    CodingStage.needs_closeout.value: "Closeout",
    CodingStage.done.value: "Done",
}

_FIELD_LABELS: dict[str, str] = {
    FieldName.kickoff.value: "Kickoff",
    FieldName.success.value: "Success",
    FieldName.approach.value: "Approach",
    FieldName.plan.value: "Plan",
    FieldName.implementation.value: "Implementation",
    FieldName.closeout.value: "Closeout",
}


def _stage_for(stage: CodingStage) -> Stage:
    if stage is CodingStage.done:
        return Stage(
            id=stage.value, label=_STAGE_LABELS[stage.value], gating_field=None, is_terminal=True
        )
    return Stage(
        id=stage.value,
        label=_STAGE_LABELS[stage.value],
        gating_field=CODING_GATING_FIELD_BY_STAGE[stage].value,
        is_terminal=False,
    )


_STAGES: tuple[Stage, ...] = tuple(_stage_for(stage) for stage in CODING_STAGE_ORDER)

_DROPPED_STAGE: Stage = Stage(
    id=CodingStage.dropped.value, label="Dropped", gating_field=None, is_terminal=True
)

_FIELDS: tuple[FieldDef, ...] = tuple(
    FieldDef(id=field.value, label=_FIELD_LABELS[field.value]) for field in FieldName
)

_WORKER_PROFILE: WorkerProfile = WorkerProfile(
    specialist_skill="panels-worker-coding",
    model=None,
    reasoning_effort=None,
    toolset_profile="default",
)

# The exact plan-handoff pair from machine.plan_handoff_status: an accepted Plan
# handed to needs_implementation under khushal becomes a durable user_takeover.
_TRANSITION_HOOKS: tuple[TransitionHook, ...] = (
    TransitionHook(
        old_stage=CodingStage.needs_plan.value,
        new_stage=CodingStage.needs_implementation.value,
        implementer=Implementer.khushal.value,
        effect=TicketStatus.user_takeover.value,
    ),
)

CODING_DEFINITION: WorkflowDefinition = WorkflowDefinition(
    type_id="coding",
    label="Coding",
    stages=_STAGES,
    dropped_stage=_DROPPED_STAGE,
    fields=_FIELDS,
    worker_profile=_WORKER_PROFILE,
    transition_hooks=_TRANSITION_HOOKS,
    supports_prefix_reconciliation=True,
)
