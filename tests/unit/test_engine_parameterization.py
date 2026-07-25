from __future__ import annotations

import inspect
import json

import pytest

from planner.core.errors import PlannerError
from planner.runtime import worker_step_readiness
from planner.tickets.contracts import AtCap, StageOwnershipMode, TicketFields
from planner.tickets.logic import admission, external_work, fields_codec, machine, resolution
from planner.worker_types.configuration import PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS
from planner.worker_types.contracts import (
    FieldDefinition,
    StageDefinition,
    WorkerProfile,
    WorkerTypeDefinition,
)
from planner.worker_types.registry import WorkerTypeRegistry

ALPHA = "".join(("needs_", "alpha"))
BETA = "".join(("needs_", "beta"))
FIELD_ALPHA = "".join(("al", "pha"))
FIELD_BETA = "".join(("be", "ta"))

SYNTHETIC_WORKER_TYPE_DEFINITION = WorkerTypeDefinition(
    worker_type="synthetic",
    label="Synthetic",
    stages=(
        StageDefinition("needs_kickoff", "Kickoff", "kickoff", False, StageOwnershipMode.worker),
        StageDefinition(ALPHA, "Alpha", FIELD_ALPHA, False, StageOwnershipMode.worker),
        StageDefinition(BETA, "Beta", FIELD_BETA, False, StageOwnershipMode.worker),
        StageDefinition("done", "Done", None, True, None),
    ),
    dropped_stage=StageDefinition("dropped", "Dropped", None, True, None),
    fields=(
        FieldDefinition("kickoff", "Kickoff"),
        FieldDefinition(FIELD_ALPHA, "Alpha"),
        FieldDefinition(FIELD_BETA, "Beta"),
    ),
    worker_profile=WorkerProfile("synthetic-worker", None, None, "default", "hermes"),
    supports_prefix_reconciliation=True,
)


def test_foreign_definition_drives_machine_semantics() -> None:
    WorkerTypeRegistry(
        (SYNTHETIC_WORKER_TYPE_DEFINITION,),
        known_skills=frozenset({"synthetic-worker"}),
        known_toolset_profiles=frozenset({"default"}),
        employee_backend_catalog=(PRODUCTION_EMPLOYEE_RUNTIME_DEFINITIONS.employee_backend_catalog),
    )
    definition = SYNTHETIC_WORKER_TYPE_DEFINITION
    assert (
        machine.auto_accept_target(
            ALPHA,
            BETA,
            FIELD_ALPHA,
            worker_type_definition=definition,
        )
        == BETA
    )
    assert machine.field_is_passed(
        FIELD_ALPHA,
        BETA,
        worker_type_definition=definition,
    )
    assert machine.at_or_beyond_ceiling(
        BETA,
        ALPHA,
        worker_type_definition=definition,
    )
    assert (
        machine.resolve_scope(
            BETA,
            "done",
            AtCap.stop,
            worker_type_definition=definition,
        ).next_ceiling
        == "done"
    )


def test_stored_decode_is_registry_free_and_declared_validation_is_explicit() -> None:
    raw = json.dumps(
        {
            "kickoff": {"value": "k", "proposal": None, "user_note": None},
            FIELD_ALPHA: {"value": None, "proposal": None, "user_note": None},
            FIELD_BETA: {"value": None, "proposal": None, "user_note": None},
            "legacy": {"value": "kept", "proposal": None, "user_note": None},
        }
    )
    stored = fields_codec.fields_from_json(raw)
    assert tuple(stored.slots) == ("kickoff", FIELD_ALPHA, FIELD_BETA, "legacy")
    declared = fields_codec.declared_fields_from_json(
        raw, SYNTHETIC_WORKER_TYPE_DEFINITION.field_ids()
    )
    assert tuple(declared.slots) == ("kickoff", FIELD_ALPHA, FIELD_BETA)
    with pytest.raises(PlannerError):
        fields_codec.declared_fields_from_json(
            json.dumps({"kickoff": {"value": None, "proposal": None, "user_note": None}}),
            SYNTHETIC_WORKER_TYPE_DEFINITION.field_ids(),
        )


@pytest.mark.parametrize(
    ("function", "args"),
    [
        (machine.field_is_passed, (FIELD_ALPHA, BETA)),
        (machine.auto_accept_target, (ALPHA, BETA, FIELD_ALPHA)),
        (machine.at_or_beyond_ceiling, (BETA, ALPHA)),
        (machine.resolve_scope, (BETA, "done", AtCap.stop)),
        (
            machine.has_pending_gating_proposal,
            (ALPHA, TicketFields.empty(SYNTHETIC_WORKER_TYPE_DEFINITION.field_ids())),
        ),
        (admission.check_agent_proposal, (ALPHA, BETA, AtCap.propose, FIELD_ALPHA)),
        (machine.has_pending_parked_proposal, (object(),)),
        (machine.effective_stage_ownership_mode, (ALPHA, {})),
        (resolution.decide_file_proposal, (object(), FIELD_ALPHA, "body", "agent", 0)),
        (
            resolution.decide_accept,
            (object(), FIELD_ALPHA, "human", None, "none", AtCap.propose),
        ),
        (resolution.decide_edit_value, (object(), FIELD_ALPHA, "body", "human")),
        (resolution.decide_return_for_revision, (object(), "human")),
        (
            resolution.decide_scope_change,
            (object(), BETA, AtCap.propose, "human"),
        ),
        (external_work.decide_external_work, (object(), BETA, {})),
        (
            worker_step_readiness.is_ready_for_worker_step,
            (object(), object()),
        ),
    ],
)
def test_semantic_functions_require_definition(function: object, args: tuple[object, ...]) -> None:
    with pytest.raises(TypeError):
        function(*args)  # type: ignore[operator]


def test_semantic_signatures_have_required_descriptive_parameter() -> None:
    functions = (
        machine.field_is_passed,
        machine.auto_accept_target,
        machine.at_or_beyond_ceiling,
        machine.resolve_scope,
        machine.has_pending_gating_proposal,
        machine.has_pending_parked_proposal,
        machine.effective_stage_ownership_mode,
        admission.check_agent_proposal,
        resolution._accept_gating_proposal,
        resolution.decide_file_proposal,
        resolution.decide_accept,
        resolution.decide_edit_value,
        resolution.decide_return_for_revision,
        resolution.decide_scope_change,
        external_work.decide_external_work,
        worker_step_readiness.is_ready_for_worker_step,
    )
    for function in functions:
        parameter = inspect.signature(function).parameters["worker_type_definition"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, function.__name__
        assert parameter.default is inspect.Parameter.empty, function.__name__


def test_readiness_requires_an_explicit_day_and_definition() -> None:
    function = worker_step_readiness.is_ready_for_worker_step
    parameters = inspect.signature(function).parameters
    for name in ("planning_day_id", "worker_type_definition"):
        parameter = parameters[name]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    with pytest.raises(TypeError):
        function(  # type: ignore[call-arg]
            object(),
            object(),
            worker_type_definition=SYNTHETIC_WORKER_TYPE_DEFINITION,
        )
    with pytest.raises(TypeError):
        function(  # type: ignore[call-arg]
            object(),
            object(),
            planning_day_id="day_2099-01-01",
        )
