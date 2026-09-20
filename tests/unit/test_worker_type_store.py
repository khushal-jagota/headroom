"""The rows that declare a Worker type: what they refuse, and what they protect.

A Worker type used to be a Python module checked when it was imported. It is a row now,
and a row can hold anything, so these tests are about the door it comes through.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.probe import (
    NEEDS_LANDING,
    PROBE_WORKER_TYPE_DEFINITION,
    seed_probe_worker_type,
    shipped_definition,
)

from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.managed_skills import read_all_skill_sources, write_skill_source
from planner.tickets import data as tickets_data
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.configuration import (
    configured_worker_type_registry,
    load_worker_runtime_definitions,
)
from planner.worker_types.contracts import FieldDefinition, StageDefinition
from planner.worker_types.store import (
    definition_from_json,
    definition_to_json,
    read_definition,
    read_definitions,
    write_definition,
)


@pytest.fixture
def database(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = connect(str(tmp_path / "planner.db"))
    create_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def test_a_database_is_seeded_with_the_worker_types_and_their_skills(
    database: sqlite3.Connection,
) -> None:
    definitions = read_definitions(database)
    assert [definition.worker_type for definition in definitions][:3] == [
        "coding",
        "general",
        "debugging",
    ]
    skills = read_all_skill_sources(database)
    for definition in definitions:
        assert definition.worker_profile.specialist_skill in skills


def test_opening_a_database_puts_its_worker_types_in_force(
    database: sqlite3.Connection,
) -> None:
    registry = configured_worker_type_registry()
    assert registry.require("coding").stage_ids()[0] == "needs_brief"

    seed_probe_worker_type(database)
    load_worker_runtime_definitions(database)

    assert "probe" in configured_worker_type_registry().registered_worker_types()


def test_a_record_survives_the_round_trip_unchanged(database: sqlite3.Connection) -> None:
    coding = read_definition(database, "coding")
    assert definition_from_json(definition_to_json(coding)) == coding


@pytest.mark.parametrize(
    "mangle",
    [
        pytest.param(lambda record: record.pop("stages"), id="a missing section"),
        pytest.param(lambda record: record.update(extra=1), id="an unknown section"),
        pytest.param(lambda record: record["stages"][0].pop("label"), id="a stage missing a key"),
        pytest.param(
            lambda record: record["stages"][0].update(ownership_mode="chief"),
            id="an unknown ownership mode",
        ),
        pytest.param(
            lambda record: record["stages"][0].update(is_terminal="yes"),
            id="terminality that is not a bool",
        ),
        pytest.param(lambda record: record.update(label=""), id="an empty label"),
        pytest.param(
            lambda record: record["profile"].update(default_model=None), id="no model named"
        ),
    ],
)
def test_the_decoder_refuses_text_that_is_not_a_worker_type(
    database: sqlite3.Connection, mangle: object
) -> None:
    record = json.loads(definition_to_json(read_definition(database, "coding")))
    mangle(record)  # type: ignore[operator]
    with pytest.raises(PlannerError) as raised:
        definition_from_json(json.dumps(record))
    assert raised.value.code is ErrorCode.validation


def test_a_type_whose_skill_is_not_stored_is_refused(database: sqlite3.Connection) -> None:
    unknown_skill = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        worker_profile=replace(
            PROBE_WORKER_TYPE_DEFINITION.worker_profile, specialist_skill="no-such-skill"
        ),
    )
    with pytest.raises(PlannerError, match="unknown skill"):
        write_definition(database, unknown_skill, now=1)


def test_a_type_that_breaks_the_registry_rules_is_refused_before_it_is_stored(
    database: sqlite3.Connection,
) -> None:
    seed_probe_worker_type(database)
    two_terminals = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=PROBE_WORKER_TYPE_DEFINITION.stages[:-1]
        + (
            StageDefinition("finished", "Finished", None, True, None),
            StageDefinition("done", "Done", None, True, None),
        ),
    )
    with pytest.raises(PlannerError, match="exactly one terminal"):
        write_definition(database, two_terminals, now=2)
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION


def _probe_ticket(conn: sqlite3.Connection, stage: str) -> str:
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="probe",
        title="A Ticket in flight",
        kickoff_note="Kickoff",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    conn.execute("UPDATE tickets SET stage = ? WHERE id = ?", (stage, ticket.id))
    return ticket.id


def test_removing_a_stage_is_refused_while_a_ticket_stands_on_it(
    database: sqlite3.Connection,
) -> None:
    seed_probe_worker_type(database)
    load_worker_runtime_definitions(database)
    ticket_id = _probe_ticket(database, "needs_alpha")

    without_alpha = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=tuple(
            stage for stage in PROBE_WORKER_TYPE_DEFINITION.stages if stage.id != "needs_alpha"
        ),
        fields=tuple(
            field for field in PROBE_WORKER_TYPE_DEFINITION.fields if field.id != "alpha"
        ),
    )
    with pytest.raises(PlannerError) as raised:
        write_definition(database, without_alpha, now=2)
    assert raised.value.detail["tickets"] == [ticket_id]
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION


def test_removing_a_field_is_refused_while_a_ticket_holds_text_in_it(
    database: sqlite3.Connection,
) -> None:
    seed_probe_worker_type(database)
    load_worker_runtime_definitions(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    database.execute(
        "UPDATE tickets SET field_values = ? WHERE id = ?",
        (json.dumps({"brief": "Kickoff", "alpha": "Work the owner wrote"}), ticket_id),
    )

    kept_stage_dropped_field = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=tuple(
            stage for stage in PROBE_WORKER_TYPE_DEFINITION.stages if stage.id != "needs_alpha"
        ),
        fields=tuple(
            field for field in PROBE_WORKER_TYPE_DEFINITION.fields if field.id != "alpha"
        ),
    )
    with pytest.raises(PlannerError) as raised:
        write_definition(database, kept_stage_dropped_field, now=2)
    assert raised.value.detail["tickets"] == [ticket_id]


def test_a_stage_no_ticket_stands_on_may_be_removed(database: sqlite3.Connection) -> None:
    seed_probe_worker_type(database)
    load_worker_runtime_definitions(database)
    _probe_ticket(database, "needs_alpha")

    without_beta = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=tuple(
            stage for stage in PROBE_WORKER_TYPE_DEFINITION.stages if stage.id != "needs_beta"
        ),
        fields=tuple(field for field in PROBE_WORKER_TYPE_DEFINITION.fields if field.id != "beta"),
    )
    write_definition(database, without_beta, now=2)

    assert "needs_beta" not in read_definition(database, "probe").stage_ids()


def test_adding_a_stage_is_free_while_tickets_are_in_flight(
    database: sqlite3.Connection,
) -> None:
    seed_probe_worker_type(database)
    load_worker_runtime_definitions(database)
    _probe_ticket(database, "needs_alpha")

    stages = PROBE_WORKER_TYPE_DEFINITION.stages
    with_gamma = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=stages[:-1]
        + (StageDefinition("needs_gamma", "Gamma", "gamma", False, StageOwnershipMode.worker),)
        + stages[-1:],
        fields=PROBE_WORKER_TYPE_DEFINITION.fields + (FieldDefinition("gamma", "Gamma"),),
    )
    write_definition(database, with_gamma, now=2)

    assert "needs_gamma" in read_definition(database, "probe").stage_ids()


def test_editing_a_type_changes_what_the_process_runs_without_a_restart(
    database: sqlite3.Connection,
) -> None:
    coding = read_definition(database, "coding")
    renamed = replace(coding, label="Coding, renamed")
    write_definition(database, renamed, now=2)
    load_worker_runtime_definitions(database)

    assert configured_worker_type_registry().require("coding").label == "Coding, renamed"


def test_a_worker_type_that_declares_no_consequences_is_refused(
    database: sqlite3.Connection,
) -> None:
    """Every Worker type ends by landing what it produced, so every one declares it.

    The refusal happens at the door, rather than when a Ticket reaches the end of its
    Stages and finds no Closeout to stand on.
    """
    seed_probe_worker_type(database)
    without_consequences = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=tuple(
            stage for stage in PROBE_WORKER_TYPE_DEFINITION.stages if stage.id != NEEDS_LANDING
        ),
        fields=tuple(
            field for field in PROBE_WORKER_TYPE_DEFINITION.fields if field.id != "consequences"
        ),
    )

    with pytest.raises(PlannerError) as caught:
        write_definition(database, without_consequences, now=2)

    assert caught.value.code is ErrorCode.validation
    assert caught.value.message == "every worker type must declare a consequences field"
    assert caught.value.detail == {"worker_type": "probe", "field": "consequences"}
    assert read_definition(database, "probe").has_field("consequences")


def test_every_seeded_type_declares_consequences(database: sqlite3.Connection) -> None:
    for definition in read_definitions(database):
        assert definition.has_field("consequences")
        assert definition.stage_gated_by("consequences") in definition.stage_ids()


def test_a_skill_can_be_added_and_a_type_declared_against_it(
    database: sqlite3.Connection,
) -> None:
    write_skill_source(
        database,
        "panels-worker-arrival",
        "---\nname: panels-worker-arrival\ndescription: A brand new worker.\n---\n\nBody.\n",
        now=1,
    )
    arrival = replace(
        shipped_definition("coding"),
        worker_type="arrival",
        label="Arrival",
        worker_profile=replace(
            shipped_definition("coding").worker_profile,
            specialist_skill="panels-worker-arrival",
        ),
    )
    write_definition(database, arrival, now=1)
    load_worker_runtime_definitions(database)

    assert "arrival" in configured_worker_type_registry().registered_worker_types()
    assert read_definition(database, "arrival").label == "Arrival"
