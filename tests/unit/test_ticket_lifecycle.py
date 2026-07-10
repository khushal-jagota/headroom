"""Lifecycle contract (D55): six linear states, five fields, ordinary gates.

Success -> Approach -> Plan -> Implementation -> Closeout -> Done. Implementation
and Closeout are ordinary gated states using the same proposal/accept machinery;
there is no separate ready/review lifecycle state and no in_progress/done jump.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from planner.tickets import data
from planner.tickets.contracts import (
    ADVANCE_TARGET,
    GATING_FIELD,
    NO_FURTHER,
    STATE_ORDER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldName,
    TicketState,
)
from planner.tickets.logic import machine

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config
    from planner.tickets.contracts import Ticket


def _create(conn: Connection, clock: TestClock, **kw: Any) -> Ticket:
    return data.create_ticket(
        conn,
        title=kw.pop("title", "Lifecycle ticket"),
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        **kw,
    )


def test_canonical_states_and_fields_are_the_five_plus_six_model() -> None:
    assert [s.value for s in TicketState] == [
        "needs_success",
        "needs_approach",
        "needs_plan",
        "needs_implementation",
        "needs_closeout",
        "done",
        "dropped",
    ]
    assert [f.value for f in FieldName] == [
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    ]
    assert STATE_ORDER == (
        TicketState.needs_success,
        TicketState.needs_approach,
        TicketState.needs_plan,
        TicketState.needs_implementation,
        TicketState.needs_closeout,
        TicketState.done,
    )


def test_each_non_terminal_state_gates_its_same_named_field() -> None:
    assert GATING_FIELD == {
        TicketState.needs_success: FieldName.success,
        TicketState.needs_approach: FieldName.approach,
        TicketState.needs_plan: FieldName.plan,
        TicketState.needs_implementation: FieldName.implementation,
        TicketState.needs_closeout: FieldName.closeout,
    }


def test_advance_is_one_linear_step_ending_at_done() -> None:
    assert ADVANCE_TARGET == {
        TicketState.needs_success: TicketState.needs_approach,
        TicketState.needs_approach: TicketState.needs_plan,
        TicketState.needs_plan: TicketState.needs_implementation,
        TicketState.needs_implementation: TicketState.needs_closeout,
        TicketState.needs_closeout: TicketState.done,
    }
    # Closeout advances to Done through ordinary machinery; ceiling does not skip
    # Closeout the way the retired in_progress/done special case skipped review.
    assert machine.advance_target(TicketState.needs_closeout) is TicketState.done
    assert (
        machine.advance_target(TicketState.needs_implementation) is TicketState.needs_closeout
    )


def test_full_linear_chain_auto_accepts_to_done(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, fake_clock)
    data.change_scope(
        tmp_db, t.id, ceiling=TicketState.done, at_cap=AtCap.propose, actor="human", now=now
    )
    chain = [
        (FieldName.success, TicketState.needs_approach),
        (FieldName.approach, TicketState.needs_plan),
        (FieldName.plan, TicketState.needs_implementation),
        (FieldName.implementation, TicketState.needs_closeout),
        (FieldName.closeout, TicketState.done),
    ]
    for fld, expected_state in chain:
        t = data.file_proposal(
            tmp_db, t.id, field=fld, body=f"{fld.value} body", actor="agent", now=now
        )
        assert t.state is expected_state
    assert t.fields.implementation.value == "implementation body"
    assert t.fields.closeout.value == "closeout body"


def test_closeout_accept_requires_human_and_reaches_done(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, fake_clock)
    data.change_scope(
        tmp_db,
        t.id,
        ceiling=TicketState.needs_closeout,
        at_cap=AtCap.propose,
        actor="human",
        now=now,
    )
    for fld in (FieldName.success, FieldName.approach, FieldName.plan, FieldName.implementation):
        t = data.file_proposal(
            tmp_db, t.id, field=fld, body=f"{fld.value} body", actor="agent", now=now
        )
    assert t.state is TicketState.needs_closeout

    # Closeout is capped: the proposal parks for human approval, no auto-accept.
    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.closeout, body="closeout body", actor="agent", now=now
    )
    assert t.state is TicketState.needs_closeout
    assert t.fields.closeout.proposal is not None
    assert t.fields.closeout.value is None

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field=FieldName.closeout,
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.state is TicketState.done
    assert t.fields.closeout.value == "closeout body"
