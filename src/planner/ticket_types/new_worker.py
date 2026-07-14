"""The ``new_worker`` WorkflowDefinition: the second shipped Worker type, whose
worker's job is to design and land ANOTHER worker.

It runs a bespoke thinking-scaffold lifecycle (deliberately NOT coding's):
``needs_kickoff -> needs_stages -> needs_thinking -> needs_drafting ->
needs_closeout -> done`` (``dropped`` reserved). The three middle stage/field
ids (``needs_stages``/``stages``, ``needs_thinking``/``thinking``,
``needs_drafting``/``drafting``) are NOVEL — they are not ``CodingStage`` /
``FieldName`` members, so they are declared here as plain strings (the same way
``tests/support/probe.py`` declares its novel ids). The shared bookend ids
(``needs_kickoff``/``kickoff``, ``needs_closeout``/``closeout``, ``done``,
``dropped``) reuse the enum ``.value``s so parity with the universal prefix/suffix
is exact.

Because the stack is fully type-driven, shipping this needs no engine/UI change —
only this definition, its specialist skill, and registration in
``coding_bridge.coding_registry()``.

No transition hooks: every stage is bounded agent work an agent completes with its
own tools; no stage hands accepted work to a human to execute, so coding's
plan-handoff ``user_takeover`` shape has no analogue here."""

from __future__ import annotations

from planner.ticket_types.contracts import (
    FieldDef,
    Stage,
    WorkerProfile,
    WorkflowDefinition,
)
from planner.tickets.contracts import CodingStage, FieldName

# The three novel middle stages/fields. Declared as plain strings because they are
# NOT CodingStage / FieldName members; the shared bookends reuse the enum values.
_STAGES: tuple[Stage, ...] = (
    Stage(
        id=CodingStage.needs_kickoff.value,
        label="Kickoff",
        gating_field=FieldName.kickoff.value,
        is_terminal=False,
    ),
    Stage(id="needs_stages", label="Stages", gating_field="stages", is_terminal=False),
    Stage(id="needs_thinking", label="Thinking", gating_field="thinking", is_terminal=False),
    Stage(id="needs_drafting", label="Drafting", gating_field="drafting", is_terminal=False),
    Stage(
        id=CodingStage.needs_closeout.value,
        label="Closeout",
        gating_field=FieldName.closeout.value,
        is_terminal=False,
    ),
    Stage(id=CodingStage.done.value, label="Done", gating_field=None, is_terminal=True),
)

_DROPPED_STAGE: Stage = Stage(
    id=CodingStage.dropped.value, label="Dropped", gating_field=None, is_terminal=True
)

_FIELDS: tuple[FieldDef, ...] = (
    FieldDef(id=FieldName.kickoff.value, label="Kickoff"),
    FieldDef(id="stages", label="Stages"),
    FieldDef(id="thinking", label="Thinking"),
    FieldDef(id="drafting", label="Drafting"),
    FieldDef(id=FieldName.closeout.value, label="Closeout"),
)

_WORKER_PROFILE: WorkerProfile = WorkerProfile(
    specialist_skill="panels-worker-new-worker",
    model=None,
    reasoning_effort=None,
    toolset_profile="default",
)

NEW_WORKER_DEFINITION: WorkflowDefinition = WorkflowDefinition(
    type_id="new_worker",
    label="New Worker",
    stages=_STAGES,
    dropped_stage=_DROPPED_STAGE,
    fields=_FIELDS,
    worker_profile=_WORKER_PROFILE,
    transition_hooks=(),
    supports_prefix_reconciliation=True,
)
