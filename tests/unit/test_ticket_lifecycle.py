"""Lifecycle contract (D108): Kickoff plus five worker stages as ordinary gates.

Kickoff -> Success -> Approach -> Plan -> Implementation -> Closeout -> Done.
Ticket title remains separate metadata; every lifecycle stage gates a field slot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from planner.tickets import data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    ExecutionRoute,
)
from planner.tickets.logic import fields_codec
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config
    from planner.tickets.contracts import Ticket


def _create(conn: Connection, clock: TestClock, **kw: Any) -> Ticket:
    return data.create_ticket(
        conn,
        worker_type="coding",
        title=kw.pop("title", "Lifecycle ticket"),
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        **kw,
    )


def test_ticket_execution_route_contract_and_nullable_create_storage(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    assert [route.value for route in ExecutionRoute] == [
        "panels_worker",
        "hermes_codex",
        "hermes_claude",
    ]
    for execution_route in (*ExecutionRoute, None):
        ticket = _create(tmp_db, fake_clock, execution_route=execution_route)
        assert ticket.execution_route is execution_route
        assert data.read_ticket(tmp_db, ticket.id).execution_route is execution_route


def test_canonical_states_and_fields_include_kickoff_as_first_ordinary_field() -> None:
    assert CODING_WORKER_TYPE_DEFINITION.stage_ids() == (
        "needs_kickoff",
        "needs_success",
        "needs_approach",
        "needs_plan",
        "needs_implementation",
        "needs_closeout",
        "done",
    )
    assert CODING_WORKER_TYPE_DEFINITION.dropped_stage.id == "dropped"
    assert CODING_WORKER_TYPE_DEFINITION.field_ids() == (
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    )
    assert CODING_WORKER_TYPE_DEFINITION.stage_ids()[1:] == (
        "needs_success",
        "needs_approach",
        "needs_plan",
        "needs_implementation",
        "needs_closeout",
        "done",
    )


def test_each_non_terminal_state_gates_its_same_named_field() -> None:
    assert {
        stage: CODING_WORKER_TYPE_DEFINITION.gating_field(stage)
        for stage in CODING_WORKER_TYPE_DEFINITION.stage_ids()[:-1]
    } == {
        "needs_kickoff": "kickoff",
        "needs_success": "success",
        "needs_approach": "approach",
        "needs_plan": "plan",
        "needs_implementation": "implementation",
        "needs_closeout": "closeout",
    }


def test_advance_is_one_linear_step_ending_at_done() -> None:
    assert {
        stage: CODING_WORKER_TYPE_DEFINITION.advance_target(stage)
        for stage in CODING_WORKER_TYPE_DEFINITION.stage_ids()[:-1]
    } == {
        "needs_kickoff": "needs_success",
        "needs_success": "needs_approach",
        "needs_approach": "needs_plan",
        "needs_plan": "needs_implementation",
        "needs_implementation": "needs_closeout",
        "needs_closeout": "done",
    }
    # Closeout advances to Done through ordinary machinery; ceiling does not skip
    # Closeout the way the retired in_progress/done special case skipped review.
    assert CODING_WORKER_TYPE_DEFINITION.advance_target("needs_closeout") == "done"
    assert CODING_WORKER_TYPE_DEFINITION.advance_target("needs_kickoff") == "needs_success"
    assert CODING_WORKER_TYPE_DEFINITION.advance_target("needs_implementation") == "needs_closeout"


def test_full_linear_chain_auto_accepts_to_done(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, fake_clock)
    data.change_scope(tmp_db, t.id, ceiling="done", at_cap=AtCap.propose, actor="human", now=now)
    chain = [
        ("kickoff", "needs_success"),
        ("success", "needs_approach"),
        ("approach", "needs_plan"),
        ("plan", "needs_implementation"),
        ("implementation", "needs_closeout"),
        ("closeout", "done"),
    ]
    for fld, expected_state in chain:
        t = data.file_proposal(tmp_db, t.id, field=fld, body=f"{fld} body", actor="agent", now=now)
        assert t.stage == expected_state
    assert fields_codec.get_slot(t.fields, "kickoff").value == "kickoff body"
    assert fields_codec.get_slot(t.fields, "implementation").value == "implementation body"
    assert fields_codec.get_slot(t.fields, "closeout").value == "closeout body"


def test_closeout_accept_requires_human_and_reaches_done(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, fake_clock)
    data.change_scope(
        tmp_db,
        t.id,
        ceiling="needs_closeout",
        at_cap=AtCap.propose,
        actor="human",
        now=now,
    )
    for fld in (
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
    ):
        t = data.file_proposal(tmp_db, t.id, field=fld, body=f"{fld} body", actor="agent", now=now)
    assert t.stage == "needs_closeout"

    # Closeout is capped: the proposal parks for human approval, no auto-accept.
    t = data.file_proposal(
        tmp_db, t.id, field="closeout", body="closeout body", actor="agent", now=now
    )
    assert t.stage == "needs_closeout"
    assert fields_codec.get_slot(t.fields, "closeout").proposal is not None
    assert fields_codec.get_slot(t.fields, "closeout").value is None

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="closeout",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.stage == "done"
    assert fields_codec.get_slot(t.fields, "closeout").value == "closeout body"
