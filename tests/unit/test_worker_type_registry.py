from __future__ import annotations

from dataclasses import replace

import pytest
from tests.support.probe import build_shipped_registry, shipped_definition

from planner.core.contracts import ErrorCode, PlannerError
from planner.worker_types.contracts import (
    FieldDefinition,
    WorkerTypeDefinition,
)
from planner.worker_types.registry import WorkerTypeRegistry

SHIPPED_REGISTRY = build_shipped_registry()
CODING_WORKER_TYPE_DEFINITION = shipped_definition("coding")

KNOWN_SKILLS = frozenset({"panels-worker", "panels-worker-coding", "panels-worker-new-worker"})
KNOWN_TOOLSETS = frozenset({"default"})


def registry(*definitions: WorkerTypeDefinition) -> WorkerTypeRegistry:
    return WorkerTypeRegistry(
        definitions,
        known_skills=KNOWN_SKILLS,
        known_toolset_profiles=KNOWN_TOOLSETS,
    )


@pytest.mark.parametrize(
    ("definition", "detail"),
    [
        (replace(CODING_WORKER_TYPE_DEFINITION, stages=()), {"worker_type": "coding"}),
        (replace(CODING_WORKER_TYPE_DEFINITION, fields=()), {"worker_type": "coding"}),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    CODING_WORKER_TYPE_DEFINITION.stages[0],
                    CODING_WORKER_TYPE_DEFINITION.stages[0],
                    *CODING_WORKER_TYPE_DEFINITION.stages[1:],
                ),
            ),
            {"worker_type": "coding", "stage": "needs_brief"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                fields=(
                    *CODING_WORKER_TYPE_DEFINITION.fields,
                    CODING_WORKER_TYPE_DEFINITION.fields[1],
                ),
            ),
            {"worker_type": "coding", "field": "success_condition"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    CODING_WORKER_TYPE_DEFINITION.stages[0],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[1], id="cancelled"),
                    *CODING_WORKER_TYPE_DEFINITION.stages[2:],
                ),
            ),
            {"worker_type": "coding", "stage": "cancelled"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[0], id="start"),
                    *CODING_WORKER_TYPE_DEFINITION.stages[1:],
                ),
            ),
            {"worker_type": "coding"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=tuple(
                    replace(stage, is_terminal=False)
                    for stage in CODING_WORKER_TYPE_DEFINITION.stages
                ),
            ),
            {"worker_type": "coding", "stage": "done"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    *CODING_WORKER_TYPE_DEFINITION.stages[:-1],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[-1], is_terminal=1),  # type: ignore[arg-type]
                ),
            ),
            {"worker_type": "coding", "stage": "done"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    *CODING_WORKER_TYPE_DEFINITION.stages[:-1],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[-1], id="finished"),
                ),
            ),
            {"worker_type": "coding", "last": "finished"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    CODING_WORKER_TYPE_DEFINITION.stages[0],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[1], id="dropped"),
                    *CODING_WORKER_TYPE_DEFINITION.stages[2:],
                ),
            ),
            {"worker_type": "coding", "stage": "dropped"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    *CODING_WORKER_TYPE_DEFINITION.stages[:-1],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[-1], gating_field="consequences"),
                ),
            ),
            {"worker_type": "coding", "stage": "done"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    CODING_WORKER_TYPE_DEFINITION.stages[0],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[1], gating_field=None),
                    *CODING_WORKER_TYPE_DEFINITION.stages[2:],
                ),
            ),
            {"worker_type": "coding", "stage": "needs_success_condition"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    CODING_WORKER_TYPE_DEFINITION.stages[0],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[1], gating_field="ghost"),
                    *CODING_WORKER_TYPE_DEFINITION.stages[2:],
                ),
            ),
            {
                "worker_type": "coding",
                "stage": "needs_success_condition",
                "gating_field": "ghost",
            },
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                stages=(
                    CODING_WORKER_TYPE_DEFINITION.stages[0],
                    replace(CODING_WORKER_TYPE_DEFINITION.stages[1], gating_field="brief"),
                    *CODING_WORKER_TYPE_DEFINITION.stages[2:],
                ),
            ),
            {"worker_type": "coding", "field": "brief"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                fields=(*CODING_WORKER_TYPE_DEFINITION.fields, FieldDefinition("ghost", "Ghost")),
            ),
            {"worker_type": "coding", "field": "ghost"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                fields=(
                    CODING_WORKER_TYPE_DEFINITION.fields[1],
                    CODING_WORKER_TYPE_DEFINITION.fields[0],
                    *CODING_WORKER_TYPE_DEFINITION.fields[2:],
                ),
            ),
            {"worker_type": "coding"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                worker_profile=replace(
                    CODING_WORKER_TYPE_DEFINITION.worker_profile, specialist_skill="ghost"
                ),
            ),
            {"worker_type": "coding", "specialist_skill": "ghost"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                worker_profile=replace(
                    CODING_WORKER_TYPE_DEFINITION.worker_profile, toolset_profile="ghost"
                ),
            ),
            {"worker_type": "coding", "toolset_profile": "ghost"},
        ),
        (
            replace(
                CODING_WORKER_TYPE_DEFINITION,
                worker_profile=replace(
                    CODING_WORKER_TYPE_DEFINITION.worker_profile, default_model="   "
                ),
            ),
            {"worker_type": "coding", "default_model": "   "},
        ),
    ],
    ids=[
        "definition has no stages",
        "definition has no fields",
        "duplicate stage id",
        "duplicate field id",
        "cancelled is not a stage",
        "brief stage and field must be paired first",
        "linear order must have exactly one terminal",
        "linear order must have exactly one terminal",
        "last stage must be done",
        "dropped is not a stage",
        "terminal stage may not gate a field",
        "non-terminal stage must gate a field",
        "gating field references an undeclared field",
        "field gated by more than one stage",
        "declared field is never gated",
        "brief stage and field must be paired first",
        "worker profile references an unknown skill",
        "worker profile references an unknown toolset profile",
        "default_model must be a non-empty string",
    ],
)
def test_registry_rejects_each_malformed_definition(
    definition: WorkerTypeDefinition,
    detail: dict[str, object],
) -> None:
    with pytest.raises(PlannerError) as raised:
        registry(definition)
    assert raised.value.code is ErrorCode.validation
    assert all(raised.value.detail.get(key) == value for key, value in detail.items())
