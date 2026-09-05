from __future__ import annotations

import json

import pytest

from planner.core.errors import PlannerError
from planner.tickets.contracts import AtCap, StageOwnershipMode
from planner.tickets.logic import fields_codec, machine
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
    worker_profile=WorkerProfile("synthetic-worker", "a-model", None, "default", "hermes"),
    supports_prefix_reconciliation=True,
)


def test_foreign_definition_drives_machine_semantics() -> None:
    WorkerTypeRegistry(
        (SYNTHETIC_WORKER_TYPE_DEFINITION,),
        known_skills=frozenset({"synthetic-worker"}),
        known_toolset_profiles=frozenset({"default"}),
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
            "kickoff": {"value": "k", "proposal": None},
            FIELD_ALPHA: {"value": None, "proposal": None},
            FIELD_BETA: {"value": None, "proposal": None},
            "legacy": {"value": "kept", "proposal": None},
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
            json.dumps({"kickoff": {"value": None, "proposal": None}}),
            SYNTHETIC_WORKER_TYPE_DEFINITION.field_ids(),
        )
