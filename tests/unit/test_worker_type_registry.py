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


def assert_error(
    definition: WorkerTypeDefinition,
    message: str,
    detail: dict[str, object],
) -> None:
    with pytest.raises(PlannerError) as raised:
        registry(definition)
    assert raised.value.code is ErrorCode.validation
    assert raised.value.message == message
    assert raised.value.detail == detail


def test_registry_validation_order_and_messages() -> None:
    base = CODING_WORKER_TYPE_DEFINITION
    assert_error(replace(base, stages=()), "definition has no stages", {"worker_type": "coding"})
    assert_error(replace(base, fields=()), "definition has no fields", {"worker_type": "coding"})
    assert_error(
        replace(base, stages=(base.stages[0], base.stages[0], *base.stages[1:])),
        "duplicate stage id",
        {"worker_type": "coding", "stage": "needs_brief"},
    )
    assert_error(
        replace(base, fields=(*base.fields, base.fields[1])),
        "duplicate field id",
        {"worker_type": "coding", "field": "success_condition"},
    )
    assert_error(
        replace(base, stages=(replace(base.stages[0], id="start"), *base.stages[1:])),
        "brief stage and field must be paired first",
        {"worker_type": "coding"},
    )
    assert_error(
        replace(
            base,
            stages=tuple(replace(stage, is_terminal=False) for stage in base.stages),
        ),
        "linear order must have exactly one terminal",
        {"worker_type": "coding", "stage": "done"},
    )
    assert_error(
        replace(
            base,
            stages=(
                *base.stages[:-1],
                replace(base.stages[-1], is_terminal=1),  # type: ignore[arg-type]
            ),
        ),
        "linear order must have exactly one terminal",
        {"worker_type": "coding", "stage": "done"},
    )
    assert_error(
        replace(base, stages=(*base.stages[:-1], replace(base.stages[-1], id="finished"))),
        "last stage must be done",
        {"worker_type": "coding", "last": "finished"},
    )
    assert_error(
        replace(
            base, stages=(base.stages[0], replace(base.stages[1], id="dropped"), *base.stages[2:])
        ),
        "dropped is not a stage",
        {"worker_type": "coding", "stage": "dropped"},
    )
    assert_error(
        replace(
            base,
            stages=(
                *base.stages[:-1],
                replace(base.stages[-1], gating_field="consequences"),
            ),
        ),
        "terminal stage may not gate a field",
        {"worker_type": "coding", "stage": "done"},
    )
    assert_error(
        replace(
            base,
            stages=(
                base.stages[0],
                replace(base.stages[1], gating_field=None),
                *base.stages[2:],
            ),
        ),
        "non-terminal stage must gate a field",
        {"worker_type": "coding", "stage": "needs_success_condition"},
    )
    assert_error(
        replace(
            base,
            stages=(
                base.stages[0],
                replace(base.stages[1], gating_field="ghost"),
                *base.stages[2:],
            ),
        ),
        "gating field references an undeclared field",
        {
            "worker_type": "coding",
            "stage": "needs_success_condition",
            "gating_field": "ghost",
        },
    )
    assert_error(
        replace(
            base,
            stages=(
                base.stages[0],
                replace(base.stages[1], gating_field="brief"),
                *base.stages[2:],
            ),
        ),
        "field gated by more than one stage",
        {"worker_type": "coding", "field": "brief"},
    )
    assert_error(
        replace(base, fields=(*base.fields, FieldDefinition("ghost", "Ghost"))),
        "declared field is never gated",
        {"worker_type": "coding", "field": "ghost"},
    )
    assert_error(
        replace(base, fields=(base.fields[1], base.fields[0], *base.fields[2:])),
        "brief stage and field must be paired first",
        {"worker_type": "coding"},
    )
    assert_error(
        replace(base, worker_profile=replace(base.worker_profile, specialist_skill="ghost")),
        "worker profile references an unknown skill",
        {"worker_type": "coding", "specialist_skill": "ghost"},
    )
    assert_error(
        replace(base, worker_profile=replace(base.worker_profile, toolset_profile="ghost")),
        "worker profile references an unknown toolset profile",
        {"worker_type": "coding", "toolset_profile": "ghost"},
    )
    assert_error(
        replace(base, worker_profile=replace(base.worker_profile, default_model="   ")),
        "default_model must be a non-empty string",
        {"worker_type": "coding", "default_model": "   "},
    )


