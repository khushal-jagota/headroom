from __future__ import annotations

import ast
import dataclasses
import inspect
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from tests.support.probe import build_probe_registry

from planner.conversation.contracts import ConversationBackendKey
from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION
from planner.worker_types.configuration import (
    PRODUCTION_WORKER_RUNTIME_DEFINITIONS,
    PRODUCTION_WORKER_TYPE_REGISTRY,
)
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION
from planner.worker_types.registry import WorkerTypeRegistry

KNOWN_SKILLS = frozenset({"panels-worker", "panels-worker-coding", "panels-worker-new-worker"})
KNOWN_TOOLSETS = frozenset({"default"})


def registry(*definitions: WorkerTypeDefinition) -> WorkerTypeRegistry:
    return WorkerTypeRegistry(
        definitions,
        known_skills=KNOWN_SKILLS,
        known_toolset_profiles=KNOWN_TOOLSETS,
    )


def test_the_agent_backends_are_a_closed_set_of_three() -> None:
    runtime_definitions = PRODUCTION_WORKER_RUNTIME_DEFINITIONS
    assert tuple(str(key) for key in ConversationBackendKey) == (
        "hermes",
        "codex",
        "claude",
    )
    assert {
        runtime_definitions.worker_type_registry.require(worker_type)
        .worker_profile.default_backend
        for worker_type in runtime_definitions.worker_type_registry.registered_worker_types()
    } == {"codex"}


def test_worker_profile_declares_complete_employee_defaults() -> None:
    for worker_type in PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types():
        profile = PRODUCTION_WORKER_TYPE_REGISTRY.require(worker_type).worker_profile
        assert profile.default_backend == "codex"
        assert profile.default_model == "gpt-5.6-sol"
        assert profile.default_reasoning_effort == "medium"


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


@pytest.mark.parametrize(
    "value",
    [
        StageDefinition("a", "A", "a", False, StageOwnershipMode.worker),
        FieldDefinition("a", "A"),
        WorkerProfile("panels-worker", None, None, "default", "hermes"),
        CODING_WORKER_TYPE_DEFINITION,
    ],
)
def test_contract_values_are_frozen_and_slot_backed(value: object) -> None:
    assert dataclasses.is_dataclass(value)
    assert not hasattr(value, "__dict__")
    field = dataclasses.fields(value)[0].name
    with pytest.raises(FrozenInstanceError):
        setattr(value, field, "changed")


def test_coding_definition_owns_complete_behavior() -> None:
    definition = CODING_WORKER_TYPE_DEFINITION
    assert definition.stage_ids() == (
        "needs_kickoff",
        "needs_success",
        "needs_approach",
        "needs_plan",
        "needs_implementation",
        "needs_closeout",
        "done",
    )
    assert definition.field_ids() == (
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    )
    assert definition.reconciliation_field_order() == definition.field_ids()
    assert definition.ceiling_range() == definition.stage_ids()
    assert definition.default_ceiling() == "needs_kickoff"
    assert definition.first_worker_stage() == "needs_success"
    assert definition.completed_stage() == "done"
    assert definition.gating_field("needs_plan") == "plan"
    assert definition.stage_gated_by("implementation") == "needs_implementation"
    assert definition.advance_target("needs_closeout") == "done"
    assert definition.advance_target("done") is None
    assert definition.is_terminal("done")
    assert definition.is_terminal("dropped")
    assert definition.gating_field("dropped") is None
    assert definition.advance_target("dropped") is None
    assert all(
        stage.default_ownership_mode is StageOwnershipMode.worker
        for stage in definition.stages
        if not stage.is_terminal
    )
    assert definition.worker_profile.specialist_skill == "panels-worker-coding"
    assert definition.worker_profile.default_backend == "codex"
    assert definition.worker_profile.default_model == "gpt-5.6-sol"
    assert definition.worker_profile.default_reasoning_effort == "medium"
    definition.validate_ticket_position("dropped", "done")


def test_new_worker_definition_has_distinct_behavior() -> None:
    definition = NEW_WORKER_TYPE_DEFINITION
    assert definition.stage_ids() == (
        "needs_kickoff",
        "needs_understanding",
        "needs_stages",
        "needs_thinking",
        "needs_runtime_defaults",
        "needs_drafting",
        "needs_closeout",
        "done",
    )
    assert definition.field_ids() == (
        "kickoff",
        "understanding",
        "stages",
        "thinking",
        "runtime_defaults",
        "drafting",
        "closeout",
    )
    assert definition.first_worker_stage() == "needs_understanding"
    assert definition.gating_field("needs_thinking") == "thinking"
    assert definition.gating_field("needs_runtime_defaults") == "runtime_defaults"


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
        "first stage must be needs_kickoff",
        {"worker_type": "coding", "first": "start"},
    )
    assert_error(
        replace(base, stages=tuple(replace(stage, is_terminal=False) for stage in base.stages)),
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
        replace(base, dropped_stage=replace(base.dropped_stage, id="discarded")),
        "dropped may not be a linear stage",
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
            stages=(base.stages[0], replace(base.stages[1], gating_field=None), *base.stages[2:]),
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
        "first field must be kickoff",
        {"worker_type": "coding", "first_field": "success"},
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
        replace(base, supports_prefix_reconciliation=1),  # type: ignore[arg-type]
        "supports_prefix_reconciliation must be a bool",
        {"worker_type": "coding"},
    )


def test_registry_is_narrow_ordered_and_defensive() -> None:
    definitions = [CODING_WORKER_TYPE_DEFINITION, NEW_WORKER_TYPE_DEFINITION]
    registered = registry(*definitions)
    definitions.clear()
    assert registered.registered_worker_types() == ("coding", "new_worker")
    assert registered.require("coding") is CODING_WORKER_TYPE_DEFINITION
    public_methods = {
        name
        for name, value in inspect.getmembers(WorkerTypeRegistry, inspect.isfunction)
        if not name.startswith("_")
    }
    assert public_methods == {"registered_worker_types", "require", "manifest"}
    with pytest.raises(PlannerError) as raised:
        registered.require("ghost")
    assert raised.value.to_payload() == {
        "error": {
            "code": "not_found",
            "message": "unknown worker type",
            "detail": {"worker_type": "ghost"},
        }
    }


def test_duplicate_worker_type_is_rejected() -> None:
    with pytest.raises(PlannerError) as raised:
        registry(CODING_WORKER_TYPE_DEFINITION, CODING_WORKER_TYPE_DEFINITION)
    assert raised.value.to_payload() == {
        "error": {
            "code": "validation",
            "message": "duplicate worker type id",
            "detail": {"worker_type": "coding"},
        }
    }


@pytest.mark.parametrize("default_backend", ["", " ", "missing-backend"])
def test_worker_profiles_require_non_empty_registered_default_backend(
    default_backend: str,
) -> None:
    definition = replace(
        CODING_WORKER_TYPE_DEFINITION,
        worker_profile=replace(
            CODING_WORKER_TYPE_DEFINITION.worker_profile,
            default_backend=default_backend,
        ),
    )
    with pytest.raises(PlannerError) as raised:
        registry(definition)
    assert raised.value.code is ErrorCode.validation
    assert raised.value.detail["backend_key"] == default_backend
    assert raised.value.detail["backend_keys"] == ["hermes", "codex", "claude"]


def test_the_probe_names_a_real_backend_of_its_own() -> None:
    # The probe exists to prove a Worker type may run on a backend the shipped types do
    # not, so its default is a real key and deliberately not the one they all name.
    probe_default = build_probe_registry().require("probe").worker_profile.default_backend
    assert ConversationBackendKey(probe_default) is ConversationBackendKey.claude
    assert probe_default not in {
        PRODUCTION_WORKER_TYPE_REGISTRY.require(worker_type).worker_profile.default_backend
        for worker_type in PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types()
    }


def test_manifests_are_complete_and_json_round_trip() -> None:
    assert PRODUCTION_WORKER_TYPE_REGISTRY.registered_worker_types() == (
        "coding",
        "new_worker",
        "exploration",
        "initiative_planning",
    )
    coding = PRODUCTION_WORKER_TYPE_REGISTRY.manifest("coding")
    assert coding == {
        "worker_type": "coding",
        "label": "Coding",
        "stages": [
            {
                "id": "needs_kickoff",
                "label": "Kickoff",
                "gating_field": "kickoff",
                "is_terminal": False,
                "default_ownership_mode": "worker",
            },
            {
                "id": "needs_success",
                "label": "Success",
                "gating_field": "success",
                "is_terminal": False,
                "default_ownership_mode": "worker",
            },
            {
                "id": "needs_approach",
                "label": "Approach",
                "gating_field": "approach",
                "is_terminal": False,
                "default_ownership_mode": "worker",
            },
            {
                "id": "needs_plan",
                "label": "Plan",
                "gating_field": "plan",
                "is_terminal": False,
                "default_ownership_mode": "worker",
            },
            {
                "id": "needs_implementation",
                "label": "Implementation",
                "gating_field": "implementation",
                "is_terminal": False,
                "default_ownership_mode": "worker",
            },
            {
                "id": "needs_closeout",
                "label": "Closeout",
                "gating_field": "closeout",
                "is_terminal": False,
                "default_ownership_mode": "worker",
            },
            {
                "id": "done",
                "label": "Done",
                "gating_field": None,
                "is_terminal": True,
                "default_ownership_mode": None,
            },
        ],
        "dropped": {
            "id": "dropped",
            "label": "Dropped",
            "gating_field": None,
            "is_terminal": True,
            "default_ownership_mode": None,
        },
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
        json.loads(json.dumps(PRODUCTION_WORKER_TYPE_REGISTRY.manifest("new_worker")))[
            "worker_type"
        ]
        == "new_worker"
    )


def test_obsolete_python_boundaries_are_absent() -> None:
    root = Path(__file__).resolve().parents[2]
    assert not (root / "src/planner/ticket_types").exists()
    assert not (root / "src/planner/tickets/logic/coding_bridge.py").exists()
    assert not (root / "src/planner/tickets/logic/ticket_type_guard.py").exists()
    assert not (root / "src/planner/tickets/logic/worker_type_guard.py").exists()


def test_old_authority_imports_and_identifiers_are_absent() -> None:
    root = Path(__file__).resolve().parents[2]
    this_file = Path(__file__).resolve()
    forbidden_identifiers = {
        "WorkflowDefinition",
        "Registry",
        "type_id",
        "CodingStage",
        "FieldName",
        "CODING_STAGE_ORDER",
        "CODING_EMPLOYEE_STAGE_ORDER",
        "CODING_GATING_FIELD_BY_STAGE",
        "CODING_NEXT_STAGE_BY_STAGE",
        "FIELD_GATES",
        "coding_definition",
        "coding_registry",
        "build_registry",
    }
    ticket_package = root / "src/planner/tickets"
    for path in (ticket_package / "contracts.py", ticket_package / "logic/machine.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        assigned_names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            for target in (node.targets if isinstance(node, ast.Assign) else (node.target,))
            if isinstance(target, ast.Name)
        }
        assert forbidden_identifiers.isdisjoint(assigned_names), path
    paths = (
        *sorted((root / "src/planner").rglob("*.py")),
        *sorted((root / "tests").rglob("*.py")),
    )
    for path in paths:
        if path.resolve() == this_file:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(
                    not alias.name.startswith("planner.ticket_types") for alias in node.names
                ), path
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("planner.ticket_types"), path
            identifiers: tuple[str | None, ...] = ()
            if isinstance(node, ast.Name):
                identifiers = (node.id,)
            elif isinstance(node, ast.arg):
                identifiers = (node.arg,)
            elif isinstance(node, ast.Attribute):
                identifiers = (node.attr,)
            elif isinstance(node, ast.keyword):
                identifiers = (node.arg,)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                identifiers = (node.name,)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                identifiers = tuple(alias.asname for alias in node.names)
            assert forbidden_identifiers.isdisjoint(
                identifier for identifier in identifiers if identifier is not None
            ), path


def test_worker_type_package_has_only_the_locked_modules_and_outbound_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    package = root / "src/planner/worker_types"
    assert {path.name for path in package.glob("*.py")} == {
        "__init__.py",
        "coding.py",
        "configuration.py",
        "contracts.py",
        "exploration.py",
        "initiative_planning.py",
        "new_worker.py",
        "registry.py",
    }
    allowed_outbound = {
        "planner.conversation.contracts",
        "planner.core.contracts",
        "planner.tickets.contracts",
    }
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules = ((node.module or ""),)
            else:
                continue
            for module in modules:
                if module.startswith("planner.") and not module.startswith("planner.worker_types"):
                    assert module in allowed_outbound, (path, module)


def test_semantic_modules_have_no_optional_definition_or_coding_fallback() -> None:
    root = Path(__file__).resolve().parents[2]
    paths = (
        root / "src/planner/tickets/logic/machine.py",
        root / "src/planner/tickets/logic/admission.py",
        root / "src/planner/tickets/logic/resolution.py",
        root / "src/planner/tickets/logic/external_work.py",
        root / "src/planner/runtime/worker_step_readiness.py",
    )
    for path in paths:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        assert '.get("coding")' not in source
        assert ".get('coding')" not in source
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            positional = (*node.args.posonlyargs, *node.args.args)
            positional_defaults = (None,) * (len(positional) - len(node.args.defaults)) + tuple(
                node.args.defaults
            )
            for argument, default in (
                *zip(positional, positional_defaults, strict=True),
                *zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True),
            ):
                if argument.arg == "worker_type_definition":
                    assert default is None, (path, node.name)
                    assert "None" not in ast.unparse(argument.annotation), (path, node.name)
                if argument.arg == "definition":
                    assert default is None, (path, node.name)
            for expression in ast.walk(node):
                if isinstance(expression, ast.BoolOp) and isinstance(expression.op, ast.Or):
                    assert not any(
                        isinstance(value, ast.Name) and value.id == "definition"
                        for value in expression.values
                    ), (path, node.name)


def test_seed_is_definition_driven_without_worker_type_fallbacks() -> None:
    root = Path(__file__).resolve().parents[2]
    seed_paths = (
        root / "src/planner/seed/__main__.py",
        root / "src/planner/seed/contracts.py",
        root / "src/planner/seed/importer.py",
        root / "src/planner/seed/logic/workspace.py",
    )
    importer_source = seed_paths[2].read_text()
    assert "TicketFields.empty(worker_type_definition.field_ids())" in importer_source
    assert (
        importer_source.count("runtime_definitions.worker_type_registry.require(worker_type)") == 1
    )
    assert "require_conversation_backend_key(" in importer_source
    assert 'require("coding")' not in importer_source
    assert "coding_worker_type_definition" not in importer_source
    complete_coding_fields = {
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    }
    for path in seed_paths:
        source = path.read_text()
        tree = ast.parse(source, filename=str(path))
        assert '"coding"' not in source
        assert "'coding'" not in source
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for argument, default in zip(
                    node.args.kwonlyargs,
                    node.args.kw_defaults,
                    strict=True,
                ):
                    if argument.arg == "worker_type":
                        assert default is None, (path, node.name)
            if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
                assert not any(
                    isinstance(value, ast.Name) and value.id == "worker_type"
                    for value in node.values
                ), (path, ast.unparse(node))
            if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
                continue
            literal_strings = {
                element.value
                for element in node.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str)
            }
            assert literal_strings != complete_coding_fields
