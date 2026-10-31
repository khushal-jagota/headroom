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

from planner.core.contracts import PlannerError
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


def test_removed_field_text_survives_unrelated_dispatched_work(
    database: sqlite3.Connection,
) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    text = json.dumps({"brief": "Kickoff", "alpha": "Work the owner wrote"})
    database.execute(
        "UPDATE tickets SET field_values = ?, worker_step_claim = 'out' WHERE id = ?",
        (text, ticket_id),
    )
    before = _row(database, ticket_id)
    result = write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "alpha"), now=2)
    after = _row(database, ticket_id)
    assert after["field_values"] == text
    assert after["worker_step_claim"] == "out"
    assert after["guidance"] == before["guidance"]
    assert [(item.ticket_id, item.old_stage, item.new_stage) for item in result] == [
        (ticket_id, "needs_beta", "needs_beta")
    ]
    assert "alpha" not in read_definition(database, "probe").field_ids()


def test_removed_stage_and_ceiling_move_back_and_discard_only_stage_feedback(
    database: sqlite3.Connection,
) -> None:
    from planner.tickets import revision_feedback

    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    database.execute("UPDATE tickets SET ceiling = 'needs_beta' WHERE id = ?", (ticket_id,))
    revision_feedback.set_feedback(
        database,
        ticket_id,
        stage="needs_beta",
        sender=OWNER_PRINCIPAL,
        message="Revise Beta",
        now=1,
    )
    before = _row(database, ticket_id)
    result = write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "beta"), now=2)
    after = _row(database, ticket_id)
    assert after["stage"] == after["ceiling"] == "needs_alpha"
    assert after["ceiling_holder"] == before["ceiling_holder"]
    assert after["field_values"] == before["field_values"]
    assert after["guidance"] == before["guidance"]
    assert (
        database.execute(
            "SELECT 1 FROM ticket_revision_feedback WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        is None
    )
    assert [
        (item.old_stage, item.new_stage, item.old_ceiling, item.new_ceiling) for item in result
    ] == [("needs_beta", "needs_alpha", "needs_beta", "needs_alpha")]


def test_finished_ticket_retains_removed_ceiling_and_text_as_readable_history(
    database: sqlite3.Connection,
) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "done")
    database.execute(
        "UPDATE tickets SET ceiling = 'needs_alpha', field_values = ? WHERE id = ?",
        (json.dumps({"brief": "Kickoff", "alpha": "Historical text"}), ticket_id),
    )
    before = _row(database, ticket_id)
    result = write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "alpha"), now=2)
    assert _row(database, ticket_id) == before
    assert result == ()
    load_worker_runtime_definitions(database)
    historical = tickets_data.read_ticket(database, ticket_id)
    assert historical.stage == "done"
    assert historical.ceiling == "needs_alpha"
    # Read tolerance cannot grant a new unfinished position or bypass canonical writes.
    definition = read_definition(database, "probe")
    with pytest.raises(PlannerError):
        definition.validate_ticket_position("needs_beta", "needs_alpha")
    with pytest.raises(PlannerError):
        definition.validate_ticket_position("done", "needs_alpha")


def test_no_earlier_destination_refuses_without_advancing(database: sqlite3.Connection) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_alpha")
    before = _row(database, ticket_id)
    replacement = _without(PROBE_WORKER_TYPE_DEFINITION, "brief", "alpha")
    with pytest.raises(PlannerError) as raised:
        write_definition(database, replacement, now=2)
    assert ticket_id in raised.value.detail["tickets"]
    assert _row(database, ticket_id) == before
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION


def test_incompatible_proposal_refuses_without_losing_body(database: sqlite3.Connection) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_alpha")
    database.execute(
        "UPDATE tickets SET pending_proposal = ? WHERE id = ?",
        (
            json.dumps(
                {
                    "field": "alpha",
                    "body": "Unapproved draft",
                    "proposed_by": "worker",
                    "created_at": 1,
                }
            ),
            ticket_id,
        ),
    )
    before = _row(database, ticket_id)
    with pytest.raises(PlannerError) as raised:
        write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "alpha"), now=2)
    assert ticket_id in raised.value.detail["tickets"]
    assert _row(database, ticket_id) == before


def test_dispatched_step_cannot_be_moved_under_its_worker(database: sqlite3.Connection) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_alpha")
    database.execute("UPDATE tickets SET worker_step_claim = 'out' WHERE id = ?", (ticket_id,))
    before = _row(database, ticket_id)
    with pytest.raises(PlannerError) as raised:
        write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "alpha"), now=2)
    assert ticket_id in raised.value.detail["tickets"]
    assert _row(database, ticket_id) == before


def test_reordered_retained_stages_refuse_authority_changes(database: sqlite3.Connection) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_alpha")
    before = _row(database, ticket_id)
    original = PROBE_WORKER_TYPE_DEFINITION
    replacement = replace(
        original,
        stages=(
            original.stages[0],
            original.stages[2],
            original.stages[1],
            *original.stages[3:],
        ),
    )
    with pytest.raises(PlannerError) as raised:
        write_definition(database, replacement, now=2)
    assert ticket_id in raised.value.detail["tickets"]
    assert _row(database, ticket_id) == before


def test_save_rolls_back_all_tickets_when_a_later_write_fails(database: sqlite3.Connection) -> None:
    _probe_database(database)
    ticket_ids = sorted(_probe_ticket(database, "needs_beta") for _ in range(2))
    before = {ticket_id: _row(database, ticket_id) for ticket_id in ticket_ids}
    database.execute(
        "CREATE TRIGGER reject_second_migration BEFORE UPDATE ON tickets "
        "WHEN NEW.id = '"
        + ticket_ids[1]
        + "' BEGIN SELECT RAISE(ABORT, 'injected migration failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected migration failure"):
        write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "beta"), now=2)
    assert {ticket_id: _row(database, ticket_id) for ticket_id in ticket_ids} == before
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION
    assert not database.in_transaction


def test_nested_savepoint_stays_inside_callers_transaction(database: sqlite3.Connection) -> None:
    _probe_database(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    before = _row(database, ticket_id)
    database.execute("BEGIN IMMEDIATE")
    write_definition(database, _without(PROBE_WORKER_TYPE_DEFINITION, "beta"), now=2)
    assert database.in_transaction
    assert _row(database, ticket_id)["stage"] == "needs_alpha"
    database.rollback()
    assert _row(database, ticket_id) == before
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION
    assert configured_worker_type_registry().require("probe") == PROBE_WORKER_TYPE_DEFINITION


def test_four_ticket_coding_amendment_copy_preserves_text_and_review_limit(
    database: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    original = read_definition(database, "coding")
    ticket_ids = []
    for index in range(4):
        ticket = tickets_data.create_ticket(
            database,
            worker_type="coding",
            title=f"Amendment copy Ticket {index}",
            brief="Original brief",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
        )
        database.execute(
            "UPDATE tickets SET stage = 'needs_what_changes', "
            "pending_proposal = NULL, ceiling = ?, "
            "field_values = ? WHERE id = ?",
            (
                "needs_plan" if index == 0 else "needs_implementation",
                json.dumps(
                    {
                        "brief": "Original brief",
                        "plan": f"Plan {index}",
                        "success_condition": f"Success {index}",
                    }
                ),
                ticket.id,
            ),
        )
        ticket_ids.append(ticket.id)
    source_before = {ticket_id: _row(database, ticket_id) for ticket_id in ticket_ids}
    copied = connect(str(tmp_path / "amendment-copy.db"))
    database.backup(copied)
    try:
        result = write_definition(copied, _without(original, "plan", "success_condition"), now=2)
        assert len(result) == 4
        for ticket_id in ticket_ids:
            after = _row(copied, ticket_id)
            assert after["stage"] == "needs_what_changes"
            assert after["field_values"] == source_before[ticket_id]["field_values"]
            assert after["ceiling_holder"] == source_before[ticket_id]["ceiling_holder"]
            assert after["guidance"] == source_before[ticket_id]["guidance"]
        first = next(item for item in result if item.ticket_id == ticket_ids[0])
        assert (first.old_ceiling, first.new_ceiling) == ("needs_plan", "needs_what_changes")
        assert _row(copied, ticket_ids[0])["ceiling"] == "needs_what_changes"
        assert {ticket_id: _row(database, ticket_id) for ticket_id in ticket_ids} == source_before
        assert read_definition(database, "coding") == original
    finally:
        copied.close()


def test_amendment_attention_uses_new_ownership_before_registry_publication(
    database: sqlite3.Connection,
) -> None:
    seed_probe_worker_type(database)
    load_worker_runtime_definitions(database)
    ticket_id = _probe_ticket(database, "needs_alpha")
    replacement = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=tuple(
            replace(stage, ownership_mode=StageOwnershipMode.user)
            if stage.id == "needs_alpha"
            else stage
            for stage in PROBE_WORKER_TYPE_DEFINITION.stages
        ),
    )
    write_definition(database, replacement, now=2)
    assert configured_worker_type_registry().require("probe") == PROBE_WORKER_TYPE_DEFINITION
    state = database.execute(
        "SELECT active FROM notification_attention_state "
        "WHERE subject_kind='ticket' AND subject_id=? AND notification_type='assigned'",
        (ticket_id,),
    ).fetchone()
    assert state[0] == 1


def test_failed_commit_rolls_back_definition_and_ticket_position(
    database: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_probe_worker_type(database)
    load_worker_runtime_definitions(database)
    ticket_id = _probe_ticket(database, "needs_beta")
    before = tuple(database.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
    replacement = replace(
        PROBE_WORKER_TYPE_DEFINITION,
        stages=tuple(s for s in PROBE_WORKER_TYPE_DEFINITION.stages if s.id != "needs_beta"),
        fields=tuple(f for f in PROBE_WORKER_TYPE_DEFINITION.fields if f.id != "beta"),
    )

    def fail_commit(self: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("injected commit failure")

    with monkeypatch.context() as patch:
        patch.setattr(type(database), "commit", fail_commit)
        with pytest.raises(sqlite3.OperationalError, match="injected commit failure"):
            write_definition(database, replacement, now=2)
    assert not database.in_transaction
    assert read_definition(database, "probe") == PROBE_WORKER_TYPE_DEFINITION
    assert (
        tuple(database.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone())
        == before
    )
