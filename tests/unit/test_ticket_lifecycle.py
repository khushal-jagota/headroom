"""Lifecycle contract (D108): Kickoff plus five worker stages as ordinary gates.

Kickoff -> Success -> Approach -> Plan -> Implementation -> Closeout -> Done.
Ticket title remains separate metadata; every lifecycle stage gates a field slot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from planner.tickets import data
from planner.tickets.contracts import (
    CODING_EMPLOYEE_STAGE_ORDER,
    CODING_GATING_FIELD_BY_STAGE,
    CODING_NEXT_STAGE_BY_STAGE,
    CODING_STAGE_ORDER,
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    CodingStage,
    FieldName,
    Implementer,
)
from planner.tickets.logic import fields_codec, machine

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


def test_ticket_implementer_contract_and_nullable_create_storage(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    assert [implementer.value for implementer in Implementer] == [
        "khushal",
        "panels_worker",
        "hermes_codex",
        "hermes_claude",
    ]
    for implementer in (*Implementer, None):
        ticket = _create(tmp_db, fake_clock, implementer=implementer)
        assert ticket.implementer is implementer
        assert data.read_ticket(tmp_db, ticket.id).implementer is implementer


def test_canonical_states_and_fields_include_kickoff_as_first_ordinary_field() -> None:
    assert [s.value for s in CodingStage] == [
        "needs_kickoff",
        "needs_success",
        "needs_approach",
        "needs_plan",
        "needs_implementation",
        "needs_closeout",
        "done",
        "dropped",
    ]
    assert [f.value for f in FieldName] == [
        "kickoff",
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    ]
    assert CODING_STAGE_ORDER == (
        CodingStage.needs_kickoff,
        CodingStage.needs_success,
        CodingStage.needs_approach,
        CodingStage.needs_plan,
        CodingStage.needs_implementation,
        CodingStage.needs_closeout,
        CodingStage.done,
    )
    assert CODING_EMPLOYEE_STAGE_ORDER == (
        CodingStage.needs_success,
        CodingStage.needs_approach,
        CodingStage.needs_plan,
        CodingStage.needs_implementation,
        CodingStage.needs_closeout,
        CodingStage.done,
    )


def test_each_non_terminal_state_gates_its_same_named_field() -> None:
    assert CODING_GATING_FIELD_BY_STAGE == {
        CodingStage.needs_kickoff: FieldName.kickoff,
        CodingStage.needs_success: FieldName.success,
        CodingStage.needs_approach: FieldName.approach,
        CodingStage.needs_plan: FieldName.plan,
        CodingStage.needs_implementation: FieldName.implementation,
        CodingStage.needs_closeout: FieldName.closeout,
    }


def test_advance_is_one_linear_step_ending_at_done() -> None:
    assert CODING_NEXT_STAGE_BY_STAGE == {
        CodingStage.needs_kickoff: CodingStage.needs_success,
        CodingStage.needs_success: CodingStage.needs_approach,
        CodingStage.needs_approach: CodingStage.needs_plan,
        CodingStage.needs_plan: CodingStage.needs_implementation,
        CodingStage.needs_implementation: CodingStage.needs_closeout,
        CodingStage.needs_closeout: CodingStage.done,
    }
    # Closeout advances to Done through ordinary machinery; ceiling does not skip
    # Closeout the way the retired in_progress/done special case skipped review.
    assert machine.advance_target(CodingStage.needs_closeout) is CodingStage.done
    assert machine.advance_target(CodingStage.needs_kickoff) is CodingStage.needs_success
    assert machine.advance_target(CodingStage.needs_implementation) is CodingStage.needs_closeout


def test_full_linear_chain_auto_accepts_to_done(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, fake_clock)
    data.change_scope(
        tmp_db, t.id, ceiling=CodingStage.done, at_cap=AtCap.propose, actor="human", now=now
    )
    chain = [
        (FieldName.kickoff, CodingStage.needs_success),
        (FieldName.success, CodingStage.needs_approach),
        (FieldName.approach, CodingStage.needs_plan),
        (FieldName.plan, CodingStage.needs_implementation),
        (FieldName.implementation, CodingStage.needs_closeout),
        (FieldName.closeout, CodingStage.done),
    ]
    for fld, expected_state in chain:
        t = data.file_proposal(
            tmp_db, t.id, field=fld, body=f"{fld.value} body", actor="agent", now=now
        )
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
        ceiling=CodingStage.needs_closeout,
        at_cap=AtCap.propose,
        actor="human",
        now=now,
    )
    for fld in (
        FieldName.kickoff,
        FieldName.success,
        FieldName.approach,
        FieldName.plan,
        FieldName.implementation,
    ):
        t = data.file_proposal(
            tmp_db, t.id, field=fld, body=f"{fld.value} body", actor="agent", now=now
        )
    assert t.stage == CodingStage.needs_closeout

    # Closeout is capped: the proposal parks for human approval, no auto-accept.
    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.closeout, body="closeout body", actor="agent", now=now
    )
    assert t.stage == CodingStage.needs_closeout
    assert fields_codec.get_slot(t.fields, "closeout").proposal is not None
    assert fields_codec.get_slot(t.fields, "closeout").value is None

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field=FieldName.closeout,
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.stage == CodingStage.done
    assert fields_codec.get_slot(t.fields, "closeout").value == "closeout body"
