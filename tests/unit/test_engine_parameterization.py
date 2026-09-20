from __future__ import annotations

import pytest

from planner.core.errors import PlannerError
from planner.tickets.contracts import StageOwnershipMode
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
        StageDefinition("needs_brief", "Kickoff", "brief", False, StageOwnershipMode.worker),
        StageDefinition(ALPHA, "Alpha", FIELD_ALPHA, False, StageOwnershipMode.worker),
        StageDefinition(BETA, "Beta", FIELD_BETA, False, StageOwnershipMode.worker),
        StageDefinition(
            "needs_consequences", "Closeout", "consequences", False, StageOwnershipMode.worker
        ),
        StageDefinition("done", "Done", None, True, None),
    ),
    fields=(
        FieldDefinition("brief", "Kickoff"),
        FieldDefinition(FIELD_ALPHA, "Alpha"),
        FieldDefinition(FIELD_BETA, "Beta"),
        FieldDefinition("consequences", "Closeout"),
    ),
    worker_profile=WorkerProfile("synthetic-worker", "a-model", None, "default", "hermes"),
)


def test_foreign_definition_drives_machine_semantics() -> None:
    WorkerTypeRegistry(
        (SYNTHETIC_WORKER_TYPE_DEFINITION,),
        known_skills=frozenset({"synthetic-worker"}),
        known_toolset_profiles=frozenset({"default"}),
    )
    definition = SYNTHETIC_WORKER_TYPE_DEFINITION
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
        machine.resolve_next_ceiling(
            BETA,
            "done",
            worker_type_definition=definition,
        )
        == "done"
    )


def test_sparse_decode_rejects_undeclared_values_instead_of_filtering_them() -> None:
    field_ids = SYNTHETIC_WORKER_TYPE_DEFINITION.field_ids()
    assert dict(fields_codec.values_from_json('{"brief":"k"}', field_ids)) == {"brief": "k"}
    assert dict(fields_codec.values_from_json("{}", field_ids)) == {}
    with pytest.raises(PlannerError):
        fields_codec.values_from_json('{"legacy":"kept"}', field_ids)
