from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tests.support.probe import build_shipped_registry, shipped_definition

from planner.conversation.contracts import ConversationBackendKey
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


def test_the_agent_backends_are_a_closed_set_of_three() -> None:
    shipped_registry = SHIPPED_REGISTRY
    assert tuple(str(key) for key in ConversationBackendKey) == (
        "hermes",
        "codex",
        "claude",
    )
    assert {
        shipped_registry.require(worker_type).worker_profile.default_backend
        for worker_type in shipped_registry.registered_worker_types()
    } == {"codex", "claude"}


def test_worker_profiles_declare_complete_employee_defaults() -> None:
    """Every shipped type names a real backend and a model. The values themselves
    belong to that type's own definition and its own test, not to a list here."""
    backends = {str(key) for key in ConversationBackendKey}
    for worker_type in SHIPPED_REGISTRY.registered_worker_types():
        profile = SHIPPED_REGISTRY.require(worker_type).worker_profile
        assert profile.default_backend in backends, worker_type
        assert profile.default_model.strip(), worker_type
        assert profile.default_reasoning_effort is None or (
            profile.default_reasoning_effort.strip()
        ), worker_type
        assert profile.specialist_skill.strip(), worker_type


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


def test_definition_errors_are_preserved() -> None:
    definition = CODING_WORKER_TYPE_DEFINITION
    with pytest.raises(PlannerError) as raised:
        definition.stage_index("ghost")
    assert raised.value.to_payload() == {
        "error": {
            "code": "validation",
            "message": "stage outside the linear order",
            "detail": {"stage": "ghost"},
        }
    }
    with pytest.raises(PlannerError) as raised:
        definition.field_definition("ghost")
    assert raised.value.to_payload() == {
        "error": {
            "code": "validation",
            "message": "unknown ticket field",
            "detail": {"field": "ghost"},
        }
    }
    with pytest.raises(PlannerError) as raised:
        definition.validate_ticket_position("done", "ghost")
    assert raised.value.to_payload() == {
        "error": {
            "code": "scope_invalid",
            "message": "ceiling outside the type's range",
            "detail": {"worker_type": "coding", "ceiling": "ghost"},
        }
    }


def test_registry_validation_order_and_messages() -> None:
    base = CODING_WORKER_TYPE_DEFINITION
    assert_error(replace(base, stages=()), "definition has no stages", {"worker_type": "coding"})
    assert_error(replace(base, fields=()), "definition has no fields", {"worker_type": "coding"})
    assert_error(
        replace(base, stages=(base.stages[0], base.stages[0], *base.stages[1:])),
        "duplicate stage id",
        {"worker_type": "coding", "stage": "needs_kickoff"},
    )
    assert_error(
        replace(base, fields=(*base.fields, base.fields[1])),
        "duplicate field id",
        {"worker_type": "coding", "field": "success"},
    )
    assert_error(
        replace(base, stages=(replace(base.stages[0], id="start"), *base.stages[1:])),
        "kickoff stage and field must be paired first",
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
                replace(base.stages[-1], gating_field="closeout"),
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
        {"worker_type": "coding", "stage": "needs_success"},
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
            "stage": "needs_success",
            "gating_field": "ghost",
        },
    )
    assert_error(
        replace(
            base,
            stages=(
                base.stages[0],
                replace(base.stages[1], gating_field="kickoff"),
                *base.stages[2:],
            ),
        ),
        "field gated by more than one stage",
        {"worker_type": "coding", "field": "kickoff"},
    )
    assert_error(
        replace(base, fields=(*base.fields, FieldDefinition("ghost", "Ghost"))),
        "declared field is never gated",
        {"worker_type": "coding", "field": "ghost"},
    )
    assert_error(
        replace(base, fields=(base.fields[1], base.fields[0], *base.fields[2:])),
        "kickoff stage and field must be paired first",
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


def test_manifests_are_complete_and_json_round_trip() -> None:
    registered = SHIPPED_REGISTRY.registered_worker_types()
    assert len(set(registered)) == len(registered)
    coding = SHIPPED_REGISTRY.manifest("coding")
    assert coding == {
        "worker_type": "coding",
        "label": "Coding",
        "stages": [
            {
                "id": "needs_kickoff",
                "label": "Kickoff",
                "gating_field": "kickoff",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "needs_success",
                "label": "Success",
                "gating_field": "success",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "needs_approach",
                "label": "Approach",
                "gating_field": "approach",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "needs_plan",
                "label": "Plan",
                "gating_field": "plan",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "needs_implementation",
                "label": "Implementation",
                "gating_field": "implementation",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "needs_closeout",
                "label": "Closeout",
                "gating_field": "closeout",
                "is_terminal": False,
                "ownership_mode": "worker",
            },
            {
                "id": "done",
                "label": "Done",
                "gating_field": None,
                "is_terminal": True,
                "ownership_mode": None,
            },
        ],
        "advance": {
            "needs_kickoff": "needs_success",
            "needs_success": "needs_approach",
            "needs_approach": "needs_plan",
            "needs_plan": "needs_implementation",
            "needs_implementation": "needs_closeout",
            "needs_closeout": "done",
        },
        "fields": [
            {"id": "kickoff", "label": "Kickoff"},
            {"id": "success", "label": "Success"},
            {"id": "approach", "label": "Approach"},
            {"id": "plan", "label": "Plan"},
            {"id": "implementation", "label": "Implementation"},
            {"id": "closeout", "label": "Closeout"},
        ],
        "ceiling_range": [
            "needs_kickoff",
            "needs_success",
            "needs_approach",
            "needs_plan",
            "needs_implementation",
            "needs_closeout",
            "done",
        ],
        "default_ceiling": "needs_kickoff",
        "worker_profile_id": "panels-worker-coding",
        "default_backend": "codex",
        "default_model": "gpt-5.6-sol",
        "default_reasoning_effort": "medium",
    }
    assert json.loads(json.dumps(coding)) == coding
    assert (
        json.loads(json.dumps(SHIPPED_REGISTRY.manifest("new_worker")))["worker_type"]
        == "new_worker"
    )
