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
    PROBE_WORKER_TYPE_DEFINITION,
    seed_probe_worker_type,
)

from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import (
    load_worker_runtime_definitions,
)
from planner.worker_types.store import (
    read_definition,
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
