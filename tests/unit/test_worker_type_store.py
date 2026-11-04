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
from typing import Any

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.probe import (
    PROBE_WORKER_TYPE_DEFINITION,
    seed_probe_worker_type,
)

from planner.core.db import connect, create_schema
from planner.tickets import data as tickets_data
from planner.tickets.contracts import StageOwnershipMode
from planner.worker_types.configuration import (
    configured_worker_type_registry,
    load_worker_runtime_definitions,
)
from planner.worker_types.contracts import WorkerTypeDefinition
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


def test_reopening_database_preserves_owner_customized_product_design(
    database: sqlite3.Connection,
) -> None:
    design = read_definition(database, "product_design")
    customized = replace(
        design,
        stages=tuple(
            replace(stage, ownership_mode=StageOwnershipMode.user)
            if stage.id in {"needs_wireframe", "needs_design"}
            else stage
            for stage in design.stages
        ),
        worker_profile=replace(
            design.worker_profile,
            default_model="sonnet",
            default_reasoning_effort="high",
        ),
    )
    write_definition(database, customized, now=2)
    database.commit()
    path = str(database.execute("PRAGMA database_list").fetchone()[2])
    reopened = connect(path)
    try:
        create_schema(reopened)
        assert read_definition(reopened, "product_design") == customized
        assert configured_worker_type_registry().require("product_design") == customized
    finally:
        reopened.close()


def _probe_ticket(conn: sqlite3.Connection, stage: str) -> str:
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="probe",
        title="A Ticket in flight",
        brief="Kickoff",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    conn.execute(
        "UPDATE tickets SET stage = ?, pending_proposal = NULL WHERE id = ?",
        (stage, ticket.id),
    )
    return ticket.id


def _without(definition: WorkerTypeDefinition, *fields: str) -> WorkerTypeDefinition:
    return replace(
        definition,
        stages=tuple(stage for stage in definition.stages if stage.gating_field not in fields),
        fields=tuple(field for field in definition.fields if field.id not in fields),
    )


def _row(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any]:
    return dict(conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone())


def _probe_database(conn: sqlite3.Connection) -> None:
    seed_probe_worker_type(conn)
    load_worker_runtime_definitions(conn)


def test_preset_edit_never_rewrites_dispatched_ticket_or_pending_approval(
    database: sqlite3.Connection,
) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    values = json.dumps({"brief": "Kickoff", "alpha": "Owner text", "old_field": "Hidden"})
    proposal = json.dumps(
        {"field": "beta", "body": "Pending answer", "proposed_by": "worker", "created_at": 1}
    )
    database.execute(
        "UPDATE tickets SET field_values=?,pending_proposal=?,pending_proposal_revision=7,"
        "worker_step_claim='out',worker_step_claim_revision=9,ceiling='needs_beta' WHERE id=?",
        (values, proposal, ticket_id),
    )
    before = _row(database, ticket_id)
    assert (
        write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "alpha", "beta"), now=2)
        == ()
    )
    assert _row(database, ticket_id) == before
    load_worker_runtime_definitions(database)
    ticket = tickets_data.read_ticket(database, ticket_id)
    assert ticket.workflow.has_field("alpha")
    assert ticket.workflow.has_field("beta")
    assert ticket.pending_proposal is not None and ticket.pending_proposal.body == "Pending answer"
    assert ticket.ceiling == "needs_beta"
    tickets_data.audit_ticket_registry_integrity(database)


def test_future_ticket_gets_replacement_preset_without_changing_existing_copy(
    database: sqlite3.Connection,
) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_alpha")
    replacement = _without(PROBE_WORKER_TYPE_DEFINITION, "alpha")
    write_definition(database, replacement, now=2)
    load_worker_runtime_definitions(database)
    future = tickets_data.create_ticket(
        database,
        worker_type="probe",
        title="Future",
        principal=OWNER_PRINCIPAL,
        now=3,
        title_max_chars=200,
    )
    assert not future.workflow.has_field("alpha")
    assert tickets_data.read_ticket(database, ticket_id).workflow.has_field("alpha")


def test_finished_ticket_retains_historical_ceiling_and_text(
    database: sqlite3.Connection,
) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "done")
    database.execute(
        "UPDATE tickets SET ceiling='old_authority',field_values=? WHERE id=?",
        (json.dumps({"alpha": "Historical text"}), ticket_id),
    )
    before = _row(database, ticket_id)
    write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "alpha"), now=2)
    assert _row(database, ticket_id) == before
    load_worker_runtime_definitions(database)
    ticket = tickets_data.read_ticket(database, ticket_id)
    assert ticket.ceiling == "old_authority"
    assert ticket.field_values["alpha"] == "Historical text"


def test_preset_save_does_not_depend_on_ticket_update_triggers(
    database: sqlite3.Connection,
) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    before = _row(database, ticket_id)
    database.execute(
        "CREATE TRIGGER no_ticket_rewrite BEFORE UPDATE ON tickets "
        "BEGIN SELECT RAISE(ABORT, 'Ticket must stay unchanged'); END"
    )
    replacement = _without(PROBE_WORKER_TYPE_DEFINITION, "beta")
    write_definition(database, replacement, now=2)
    assert _row(database, ticket_id) == before
    assert read_definition(database, "probe") == replacement


def test_nested_savepoint_stays_inside_callers_transaction(database: sqlite3.Connection) -> None:
    _probe_database(database)
    replacement = _without(PROBE_WORKER_TYPE_DEFINITION, "beta")
    database.execute("BEGIN IMMEDIATE")
    write_definition(database, replacement, now=2)
    assert database.in_transaction
    database.rollback()
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION


def test_failed_commit_rolls_back_preset_and_preserves_ticket(
    database: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    before = _row(database, ticket_id)
    replacement = _without(PROBE_WORKER_TYPE_DEFINITION, "beta")

    def fail_commit(self: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("injected commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(type(database), "commit", fail_commit)
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            write_definition(database, replacement, now=2)
    assert not database.in_transaction
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION
    assert _row(database, ticket_id) == before
