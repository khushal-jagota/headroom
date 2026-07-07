"""Acceptance items 2, 3, 4, 5, 6, 7, 8, 13, 36 for the tickets engine (SPEC §4.4).

Drives everything through planner.tickets.data against a real temp SQLite DB via
the shared conftest fixtures. No mocks. Assertions pin the frozen states, event
payloads, event order, and error codes from the T04 plan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from planner.core.contracts import EventKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import read_events_since
from planner.tickets import data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldName,
    TicketState,
    TicketStatus,
)
from planner.tickets.logic import machine

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config
    from planner.core.contracts import EventRow
    from planner.tickets.contracts import Ticket


def _create(conn: Connection, cfg: Config, clock: TestClock, **kw: Any) -> Ticket:
    return data.create_ticket(
        conn,
        title=kw.pop("title", "Test ticket"),
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        **kw,
    )


def _scope(
    conn: Connection, t: Ticket, ceiling: TicketState, at_cap: AtCap, clock: TestClock
) -> Ticket:
    return data.change_scope(
        conn, t.id, ceiling=ceiling, at_cap=at_cap, actor="human", now=clock.now_unix()
    )


def _events(
    conn: Connection, cfg: Config, ticket_id: str, kind: EventKind | None = None
) -> list[EventRow]:
    rows = read_events_since(conn, 0, cfg.events_read_limit)
    return [
        e for e in rows if e.entity_id == ticket_id and (kind is None or e.kind == kind.value)
    ]


def test_ticket_status_transitions(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    assert t.ticket_status is TicketStatus.empty

    guard_calls = 0

    def guard(conn: Connection, ticket: Ticket) -> bool:
        nonlocal guard_calls
        guard_calls += 1
        return True

    started = data.start_run_if_runnable(tmp_db, t.id, guard=guard, now=now)
    assert started is not None
    assert started.ticket_status is TicketStatus.agent_running_step
    assert guard_calls == 1

    skipped = data.start_run_if_runnable(tmp_db, t.id, guard=guard, now=now)
    assert skipped is None
    assert guard_calls == 1  # non-empty status skips before the readiness guard

    t = data.finish_run_if_still_running_step(tmp_db, t.id, session_key="sess-1", now=now)
    assert t.ticket_status is TicketStatus.empty
    assert t.chat_session_key == "sess-1"

    t = data.start_run_if_runnable(tmp_db, t.id, guard=None, now=now)
    assert t is not None
    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.success, body="parked", actor="agent", now=now
    )
    assert t.ticket_status is TicketStatus.awaiting_approval
    t = data.finish_run_if_still_running_step(tmp_db, t.id, session_key="sess-2", now=now)
    assert t.ticket_status is TicketStatus.awaiting_approval
    assert t.chat_session_key == "sess-2"

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field=FieldName.success,
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.ticket_status is TicketStatus.empty

    t = data.take_over_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.user_takeover
    skipped = data.start_run_if_runnable(tmp_db, t.id, guard=guard, now=now)
    assert skipped is None
    assert guard_calls == 1

    t = data.release_ticket(tmp_db, t.id, now=now)
    assert t.ticket_status is TicketStatus.empty
    t = data.mark_run_errored(tmp_db, t.id, error="boom", session_key="sess-3", now=now)
    assert t.ticket_status is TicketStatus.errored
    assert t.chat_session_key == "sess-3"

    status_events = _events(tmp_db, cfg, t.id, EventKind.ticket_status_changed)
    assert all("worker" not in e.payload for e in status_events)
    assert status_events[-1].payload == {"ticket_status": "errored", "error": "boom"}


def test_auto_accepted_proposal_does_not_park_status(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, TicketState.needs_plan, AtCap.propose, fake_clock)
    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.success, body="success", actor="agent", now=now
    )
    assert t.state is TicketState.needs_approach
    assert t.ticket_status is TicketStatus.empty
    status_events = _events(tmp_db, cfg, t.id, EventKind.ticket_status_changed)
    assert status_events == []


def test_a02_gating_chain_one_state_per_accept(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, TicketState.needs_review, AtCap.propose, fake_clock)

    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.success, body="success body", actor="agent", now=now
    )
    assert t.state is TicketState.needs_approach
    assert t.fields.success.value == "success body"
    assert t.fields.success.proposal is None
    assert len(_events(tmp_db, cfg, t.id, EventKind.state_changed)) == 1

    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.approach, body="approach body", actor="agent", now=now
    )
    assert t.state is TicketState.needs_plan
    assert len(_events(tmp_db, cfg, t.id, EventKind.state_changed)) == 2

    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.plan, body="plan body", actor="agent", now=now
    )
    assert t.state is TicketState.in_progress
    assert len(_events(tmp_db, cfg, t.id, EventKind.state_changed)) == 3

    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.result, body="result body", actor="agent", now=now
    )
    assert t.state is TicketState.needs_review
    assert len(_events(tmp_db, cfg, t.id, EventKind.state_changed)) == 4

    changes = _events(tmp_db, cfg, t.id, EventKind.state_changed)
    assert [(e.payload["from"], e.payload["to"]) for e in changes] == [
        ("needs_success", "needs_approach"),
        ("needs_approach", "needs_plan"),
        ("needs_plan", "in_progress"),
        ("in_progress", "needs_review"),
    ]
    for e in changes:
        assert e.payload["cause"] == "auto_accept"
        assert set(e.payload.keys()) == {"from", "to", "cause"}
    for e in _events(tmp_db, cfg, t.id, EventKind.proposal_accepted):
        assert e.payload["resolved_by"] == "auto"
        assert e.payload["edited"] is False


def test_a03_ceiling_auto_accept_until_cap_then_pending(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, TicketState.needs_plan, AtCap.propose, fake_clock)

    t = data.file_proposal(tmp_db, t.id, field=FieldName.success, body="s", actor="agent", now=now)
    assert t.state is TicketState.needs_approach
    t = data.file_proposal(tmp_db, t.id, field=FieldName.approach, body="a", actor="agent", now=now)
    assert t.state is TicketState.needs_plan

    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.plan, body="plan body", actor="agent", now=now
    )
    assert t.state is TicketState.needs_plan
    assert t.fields.plan.value is None
    assert t.fields.plan.proposal is not None
    assert t.fields.plan.proposal.body == "plan body"
    assert t.fields.plan.proposal.proposed_by == "agent"
    assert machine.has_pending_gating_proposal(t.state, t.fields) is True

    assert len(_events(tmp_db, cfg, t.id, EventKind.state_changed)) == 2
    filed = _events(tmp_db, cfg, t.id, EventKind.proposal_filed)
    assert len(filed) == 1
    assert filed[0].payload == {"field": "plan", "body": "plan body", "proposed_by": "agent"}


def test_a04_at_cap_stop_vs_propose(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    a = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, a, TicketState.needs_approach, AtCap.propose, fake_clock)
    a = data.file_proposal(tmp_db, a.id, field=FieldName.success, body="s", actor="agent", now=now)
    assert a.state is TicketState.needs_approach
    _scope(tmp_db, a, TicketState.needs_approach, AtCap.stop, fake_clock)
    count_at_stop = len(_events(tmp_db, cfg, a.id))

    with pytest.raises(PlannerError) as exc_gating:
        data.file_proposal(tmp_db, a.id, field=FieldName.approach, body="x", actor="agent", now=now)
    assert exc_gating.value.code is ErrorCode.at_cap_stop
    assert exc_gating.value.detail["gating_field"] == "approach"

    with pytest.raises(PlannerError) as exc_non_gating:
        data.file_proposal(tmp_db, a.id, field=FieldName.plan, body="x", actor="agent", now=now)
    assert exc_non_gating.value.code is ErrorCode.at_cap_stop

    a = data.read_ticket(tmp_db, a.id)
    assert a.state is TicketState.needs_approach
    assert a.fields.approach.proposal is None
    assert len(_events(tmp_db, cfg, a.id)) == count_at_stop

    _scope(tmp_db, a, TicketState.needs_approach, AtCap.propose, fake_clock)
    a = data.file_proposal(
        tmp_db, a.id, field=FieldName.approach, body="approach draft", actor="agent", now=now
    )
    assert a.state is TicketState.needs_approach
    assert a.fields.approach.proposal is not None
    assert a.fields.approach.proposal.body == "approach draft"
    assert a.fields.approach.value is None

    count_before = len(_events(tmp_db, cfg, a.id))
    with pytest.raises(PlannerError) as exc_propose_non_gating:
        data.file_proposal(tmp_db, a.id, field=FieldName.plan, body="x", actor="agent", now=now)
    assert exc_propose_non_gating.value.code is ErrorCode.validation
    a = data.read_ticket(tmp_db, a.id)
    assert a.state is TicketState.needs_approach
    assert len(_events(tmp_db, cfg, a.id)) == count_before

    b = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, b, TicketState.needs_plan, AtCap.stop, fake_clock)
    c = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, c, TicketState.needs_plan, AtCap.propose, fake_clock)

    b = data.file_proposal(tmp_db, b.id, field=FieldName.success, body="s", actor="agent", now=now)
    c = data.file_proposal(tmp_db, c.id, field=FieldName.success, body="s", actor="agent", now=now)
    assert b.state is TicketState.needs_approach
    assert c.state is TicketState.needs_approach
    assert b.fields.success.value == c.fields.success.value == "s"

    b = data.file_proposal(tmp_db, b.id, field=FieldName.plan, body="p", actor="agent", now=now)
    c = data.file_proposal(tmp_db, c.id, field=FieldName.plan, body="p", actor="agent", now=now)
    assert b.fields.plan.proposal is not None and b.fields.plan.value is None
    assert c.fields.plan.proposal is not None and c.fields.plan.value is None
    assert b.state is TicketState.needs_approach
    assert c.state is TicketState.needs_approach


def test_a05_one_pending_proposal_per_field_supersede(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.success, body="first body", actor="agent", now=now
    )
    assert t.fields.success.proposal is not None
    assert t.fields.success.proposal.body == "first body"

    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.success, body="second body", actor="agent", now=now
    )
    assert t.fields.success.proposal is not None
    assert t.fields.success.proposal.body == "second body"

    superseded = _events(tmp_db, cfg, t.id, EventKind.proposal_superseded)
    assert len(superseded) == 1
    assert superseded[0].payload == {"field": "success", "replaced_body": "first body"}

    filed = _events(tmp_db, cfg, t.id, EventKind.proposal_filed)
    assert len(filed) == 2
    assert superseded[0].id < filed[1].id

    assert t.state is TicketState.needs_success
    assert len(_events(tmp_db, cfg, t.id, EventKind.state_changed)) == 0


def test_a06_edit_accept_stores_edited_text(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.success, body="draft body", actor="agent", now=now
    )

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field=FieldName.success,
        actor="human",
        now=now,
        edited_body="edited body exactly",
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    assert t.fields.success.value == "edited body exactly"
    assert t.fields.success.proposal is None

    accepted = _events(tmp_db, cfg, t.id, EventKind.proposal_accepted)
    assert accepted[-1].payload == {
        "field": "success",
        "body": "edited body exactly",
        "resolved_by": "human",
        "edited": True,
    }

    assert t.state is TicketState.needs_approach
    changed = _events(tmp_db, cfg, t.id, EventKind.state_changed)
    assert changed[-1].payload == {
        "from": "needs_success",
        "to": "needs_approach",
        "cause": "human_accept",
    }
    assert t.ceiling is TicketState.needs_approach
    assert t.at_cap is AtCap.propose
    scope = _events(tmp_db, cfg, t.id, EventKind.scope_changed)
    assert scope[-1].payload == {
        "ceiling": "needs_approach",
        "at_cap": "propose",
        "cause": "onward_scope",
    }


def test_a07_result_routing(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()

    t1 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t1, TicketState.needs_review, AtCap.propose, fake_clock)
    for f, body in [(FieldName.success, "s"), (FieldName.approach, "a"), (FieldName.plan, "p")]:
        t1 = data.file_proposal(tmp_db, t1.id, field=f, body=body, actor="agent", now=now)
    assert t1.state is TicketState.in_progress
    t1 = data.file_proposal(tmp_db, t1.id, field=FieldName.result, body="r", actor="agent", now=now)
    assert t1.state is TicketState.needs_review
    assert t1.fields.result.value == "r"

    t1 = data.approve_review(tmp_db, t1.id, actor="human", now=now)
    assert t1.state is TicketState.done
    changed = _events(tmp_db, cfg, t1.id, EventKind.state_changed)
    assert changed[-1].payload == {
        "from": "needs_review",
        "to": "done",
        "cause": "review_approve",
    }
    assert t1.ceiling is TicketState.needs_review
    assert t1.at_cap is AtCap.propose

    t2 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t2, TicketState.done, AtCap.propose, fake_clock)
    for f, body in [
        (FieldName.success, "s"),
        (FieldName.approach, "a"),
        (FieldName.plan, "p"),
        (FieldName.result, "r"),
    ]:
        t2 = data.file_proposal(tmp_db, t2.id, field=f, body=body, actor="agent", now=now)
    assert t2.state is TicketState.done
    seq = [
        (e.payload["from"], e.payload["to"])
        for e in _events(tmp_db, cfg, t2.id, EventKind.state_changed)
    ]
    assert seq == [
        ("needs_success", "needs_approach"),
        ("needs_approach", "needs_plan"),
        ("needs_plan", "in_progress"),
        ("in_progress", "done"),
    ]

    t3 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t3, TicketState.in_progress, AtCap.propose, fake_clock)
    for f, body in [(FieldName.success, "s"), (FieldName.approach, "a"), (FieldName.plan, "p")]:
        t3 = data.file_proposal(tmp_db, t3.id, field=f, body=body, actor="agent", now=now)
    assert t3.state is TicketState.in_progress
    t3 = data.file_proposal(tmp_db, t3.id, field=FieldName.result, body="r", actor="agent", now=now)
    assert t3.state is TicketState.in_progress
    assert t3.fields.result.proposal is not None

    _scope(tmp_db, t3, TicketState.done, AtCap.propose, fake_clock)
    t3 = data.accept_proposal(
        tmp_db,
        t3.id,
        field=FieldName.result,
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.stop,
    )
    assert t3.state is TicketState.done


def test_a08_recap_rules(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)

    with pytest.raises(PlannerError) as exc_early:
        data.write_recap(tmp_db, t.id, body="too early", actor="agent", now=now)
    assert exc_early.value.code is ErrorCode.recap_too_early
    assert data.read_ticket(tmp_db, t.id).recap == ""

    _scope(tmp_db, t, TicketState.needs_approach, AtCap.propose, fake_clock)
    t = data.file_proposal(tmp_db, t.id, field=FieldName.success, body="s", actor="agent", now=now)
    assert t.state is TicketState.needs_approach

    t = data.write_recap(tmp_db, t.id, body="first recap", actor="agent", now=now)
    assert t.recap == "first recap"
    assert len(_events(tmp_db, cfg, t.id, EventKind.recap_updated)) == 1

    t = data.write_recap(tmp_db, t.id, body="second recap", actor="agent", now=now)
    assert t.recap == "second recap"
    assert len(_events(tmp_db, cfg, t.id, EventKind.recap_updated)) == 2
    assert t.state is TicketState.needs_approach
    assert len(_events(tmp_db, cfg, t.id, EventKind.state_changed)) == 1

    t = data.drop_ticket(tmp_db, t.id, actor="human", now=now)
    assert t.state is TicketState.dropped
    with pytest.raises(PlannerError) as exc_dropped:
        data.write_recap(tmp_db, t.id, body="post-drop recap", actor="agent", now=now)
    assert exc_dropped.value.code is ErrorCode.recap_too_early
    assert exc_dropped.value.detail["state"] == "dropped"
    assert data.read_ticket(tmp_db, t.id).recap == "second recap"


def test_a13_sprint_assignment_rules(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    tmp_db.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES ('sp_test', 'Test sprint', '2026-07-01', '2026-07-12', ?, ?)",
        (now, now),
    )

    standalone = _create(tmp_db, cfg, fake_clock)
    standalone = data.set_sprint(tmp_db, standalone.id, sprint_id="sp_test", actor="agent", now=now)
    assert data.read_ticket(tmp_db, standalone.id).sprint_id == "sp_test"
    upd = _events(tmp_db, cfg, standalone.id, EventKind.ticket_updated)
    assert upd[-1].payload == {"field": "sprint_id", "from": None, "to": "sp_test"}

    tmp_db.execute(
        "INSERT INTO sprint_items (id, title, project, sprint_id, created_at, updated_at) "
        "VALUES ('si_test', 'Parent item', 'Vylo', 'sp_test', ?, ?)",
        (now, now),
    )
    parented = _create(tmp_db, cfg, fake_clock, sprint_item_id="si_test")

    with pytest.raises(PlannerError) as exc:
        data.set_sprint(tmp_db, parented.id, sprint_id="sp_test", actor="human", now=now)
    assert exc.value.code is ErrorCode.sprint_derived
    assert data.read_ticket(tmp_db, parented.id).sprint_id is None
    assert _events(tmp_db, cfg, parented.id, EventKind.ticket_updated) == []

    assert data.get_effective_sprint_id(tmp_db, parented.id) == "sp_test"
    assert data.get_effective_sprint_id(tmp_db, standalone.id) == "sp_test"


def test_a36_onward_scope(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()

    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_proposal(
        tmp_db, t.id, field=FieldName.success, body="body", actor="agent", now=now
    )
    assert t.fields.success.proposal is not None
    count = len(_events(tmp_db, cfg, t.id))

    with pytest.raises(PlannerError) as e_missing_ceiling:
        data.accept_proposal(
            tmp_db, t.id, field=FieldName.success, actor="human", now=now,
            next_ceiling=None, at_cap=AtCap.stop,
        )
    assert e_missing_ceiling.value.code is ErrorCode.scope_missing
    with pytest.raises(PlannerError) as e_missing_at_cap:
        data.accept_proposal(
            tmp_db, t.id, field=FieldName.success, actor="human", now=now,
            next_ceiling=NO_FURTHER, at_cap=None,
        )
    assert e_missing_at_cap.value.code is ErrorCode.scope_missing

    t = data.read_ticket(tmp_db, t.id)
    assert t.state is TicketState.needs_success
    assert t.fields.success.value is None
    assert t.fields.success.proposal is not None
    assert t.fields.success.proposal.body == "body"
    assert t.ceiling is TicketState.needs_success
    assert t.at_cap is AtCap.propose
    assert len(_events(tmp_db, cfg, t.id)) == count

    with pytest.raises(PlannerError) as e_before:
        data.accept_proposal(
            tmp_db, t.id, field=FieldName.success, actor="human", now=now,
            next_ceiling=TicketState.needs_success, at_cap=AtCap.propose,
        )
    assert e_before.value.code is ErrorCode.scope_invalid
    with pytest.raises(PlannerError) as e_dropped:
        data.accept_proposal(
            tmp_db, t.id, field=FieldName.success, actor="human", now=now,
            next_ceiling=TicketState.dropped, at_cap=AtCap.propose,
        )
    assert e_dropped.value.code is ErrorCode.scope_invalid
    assert data.read_ticket(tmp_db, t.id).state is TicketState.needs_success
    assert len(_events(tmp_db, cfg, t.id)) == count

    with pytest.raises(PlannerError) as e_agent:
        data.accept_proposal(
            tmp_db, t.id, field=FieldName.success, actor="agent", now=now,
            next_ceiling=NO_FURTHER, at_cap=AtCap.propose,
        )
    assert e_agent.value.code is ErrorCode.agent_forbidden
    assert len(_events(tmp_db, cfg, t.id)) == count

    t = data.accept_proposal(
        tmp_db, t.id, field=FieldName.success, actor="human", now=now,
        next_ceiling=NO_FURTHER, at_cap=AtCap.stop,
    )
    assert t.state is TicketState.needs_approach
    assert t.ceiling is TicketState.needs_approach
    assert t.at_cap is AtCap.stop
    with pytest.raises(PlannerError) as e_rest:
        data.file_proposal(tmp_db, t.id, field=FieldName.approach, body="x", actor="agent", now=now)
    assert e_rest.value.code is ErrorCode.at_cap_stop

    t2 = _create(tmp_db, cfg, fake_clock)
    t2 = data.file_proposal(
        tmp_db, t2.id, field=FieldName.success, body="body", actor="agent", now=now
    )
    t2 = data.accept_proposal(
        tmp_db, t2.id, field=FieldName.success, actor="human", now=now,
        next_ceiling=NO_FURTHER, at_cap=AtCap.propose,
    )
    assert t2.state is TicketState.needs_approach
    t2 = data.file_proposal(
        tmp_db, t2.id, field=FieldName.approach, body="draft", actor="agent", now=now
    )
    assert t2.fields.approach.proposal is not None
    assert t2.state is TicketState.needs_approach

    t3 = _create(tmp_db, cfg, fake_clock)
    t3 = data.file_proposal(
        tmp_db, t3.id, field=FieldName.success, body="body", actor="agent", now=now
    )
    t3 = data.accept_proposal(
        tmp_db, t3.id, field=FieldName.success, actor="human", now=now,
        next_ceiling=TicketState.needs_plan, at_cap=AtCap.propose,
    )
    assert t3.ceiling is TicketState.needs_plan
    scope_count_before = len(_events(tmp_db, cfg, t3.id, EventKind.scope_changed))
    t3 = data.file_proposal(
        tmp_db, t3.id, field=FieldName.approach, body="a", actor="agent", now=now
    )
    assert t3.state is TicketState.needs_plan
    assert t3.ceiling is TicketState.needs_plan
    assert t3.at_cap is AtCap.propose
    assert len(_events(tmp_db, cfg, t3.id, EventKind.scope_changed)) == scope_count_before

    t4 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t4, TicketState.needs_review, AtCap.propose, fake_clock)
    for f, body in [
        (FieldName.success, "s"),
        (FieldName.approach, "a"),
        (FieldName.plan, "p"),
        (FieldName.result, "r"),
    ]:
        t4 = data.file_proposal(tmp_db, t4.id, field=f, body=body, actor="agent", now=now)
    assert t4.state is TicketState.needs_review
    ceiling_before = t4.ceiling
    at_cap_before = t4.at_cap
    scopes_before = len(_events(tmp_db, cfg, t4.id, EventKind.scope_changed))
    t4 = data.approve_review(tmp_db, t4.id, actor="human", now=now)
    assert t4.state is TicketState.done
    assert t4.ceiling is ceiling_before
    assert t4.at_cap is at_cap_before
    assert len(_events(tmp_db, cfg, t4.id, EventKind.scope_changed)) == scopes_before
