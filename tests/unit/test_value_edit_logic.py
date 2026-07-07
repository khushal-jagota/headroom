"""Pure-logic tests for resolution.decide_edit_value (Decision B: the human edit of
an already-passed settled field value) plus the decide_accept dropped-guard. Values
stay written solely by the resolution engine; these pin the tightly-guarded human
write path and its rejections. Supporting tests, no §18.3 anchor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from planner.core.contracts import EventKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import read_events_since
from planner.tickets import data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldName,
    FieldSlot,
    Proposal,
    Ticket,
    TicketFields,
    TicketState,
    TicketStatus,
)
from planner.tickets.logic import resolution

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config
    from planner.core.contracts import EventRow


def _fields(**slots: FieldSlot) -> TicketFields:
    return TicketFields(
        success=slots.get("success", FieldSlot()),
        approach=slots.get("approach", FieldSlot()),
        plan=slots.get("plan", FieldSlot()),
        result=slots.get("result", FieldSlot()),
    )


def _ticket(
    state: TicketState, fields: TicketFields, *, ceiling: TicketState = TicketState.done
) -> Ticket:
    return Ticket(
        id="t_test",
        title="T",
        state=state,
        priority=Priority.P3,
        deadline=None,
        project=None,
        sprint_item_id=None,
        sprint_id=None,
        recap="",
        ceiling=ceiling,
        at_cap=AtCap.propose,
        ticket_status=TicketStatus.empty,
        chat_session_key=None,
        alias=None,
        fields=fields,
        created_at=0,
        updated_at=0,
    )


def _events(
    conn: Connection, cfg: Config, ticket_id: str, kind: EventKind | None = None
) -> list[EventRow]:
    rows = read_events_since(conn, 0, cfg.events_read_limit)
    return [
        e for e in rows if e.entity_id == ticket_id and (kind is None or e.kind == kind.value)
    ]


def _passed_ticket(conn: Connection, cfg: Config, clock: TestClock) -> Ticket:
    """A real ticket driven to needs_plan with success & approach settled (values),
    plan the current gating field (unset)."""
    now = clock.now_unix()
    t = data.create_ticket(
        conn, title="T", actor="human", now=now, title_max_chars=TITLE_MAX_CHARS
    )
    t = data.change_scope(
        conn, t.id, ceiling=TicketState.needs_plan, at_cap=AtCap.propose, actor="human", now=now
    )
    t = data.file_proposal(conn, t.id, field=FieldName.success, body="success v1", actor="agent",
                           now=now)
    t = data.file_proposal(conn, t.id, field=FieldName.approach, body="approach v1", actor="agent",
                           now=now)
    assert t.state is TicketState.needs_plan
    assert t.fields.success.value == "success v1"
    assert t.fields.approach.value == "approach v1"
    return t


def test_edit_passed_field_succeeds(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # Decision shape: exactly one field_value_edited event; state/ceiling/at_cap left None.
    ticket = _ticket(
        TicketState.needs_plan,
        _fields(success=FieldSlot(value="old success", notes="keep me")),
        ceiling=TicketState.needs_plan,
    )
    decision = resolution.decide_edit_value(ticket, FieldName.success, "new success", "human")
    assert decision.new_state is None
    assert decision.new_ceiling is None
    assert decision.new_at_cap is None
    assert len(decision.events) == 1
    assert decision.events[0].kind is EventKind.field_value_edited
    assert decision.events[0].payload == {"field": "success", "body": "new success"}
    assert decision.new_fields is not None
    assert decision.new_fields.success.value == "new success"
    assert decision.new_fields.success.notes == "keep me"  # notes preserved

    # End-to-end through the sole appender: value persists, state/ceiling untouched,
    # one field_value_edited row logged.
    now = fake_clock.now_unix()
    t = _passed_ticket(tmp_db, cfg, fake_clock)
    t = data.edit_field_value(
        tmp_db, t.id, field=FieldName.success, new_body="success EDITED", actor="human", now=now
    )
    assert t.fields.success.value == "success EDITED"
    assert t.state is TicketState.needs_plan
    assert t.ceiling is TicketState.needs_plan
    assert t.at_cap is AtCap.propose
    logged = _events(tmp_db, cfg, t.id, EventKind.field_value_edited)
    assert len(logged) == 1
    assert logged[0].payload == {"field": "success", "body": "success EDITED"}


def test_edit_unset_value_rejected() -> None:
    ticket = _ticket(
        TicketState.needs_approach, _fields(success=FieldSlot(value=None))
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(ticket, FieldName.success, "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "success"}


def test_edit_field_with_live_proposal_rejected() -> None:
    ticket = _ticket(
        TicketState.needs_plan,
        _fields(
            success=FieldSlot(
                value="settled", proposal=Proposal(body="pending", proposed_by="agent",
                                                    created_at=0)
            )
        ),
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(ticket, FieldName.success, "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "success"}


def test_edit_current_gating_field_rejected() -> None:
    # plan gates needs_plan; at needs_plan it is not yet passed even with a settled value.
    ticket = _ticket(
        TicketState.needs_plan,
        _fields(plan=FieldSlot(value="plan value")),
        ceiling=TicketState.needs_plan,
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(ticket, FieldName.plan, "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "plan", "state": "needs_plan"}


def test_edit_future_field_with_value_rejected() -> None:
    # Backward-state-jump hazard: state=needs_approach but plan already holds a value.
    ticket = _ticket(
        TicketState.needs_approach,
        _fields(plan=FieldSlot(value="plan value")),
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(ticket, FieldName.plan, "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "plan", "state": "needs_approach"}


def test_edit_dropped_ticket_rejected() -> None:
    ticket = _ticket(
        TicketState.dropped, _fields(success=FieldSlot(value="settled"))
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(ticket, FieldName.success, "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"state": "dropped"}


def test_edit_agent_actor_forbidden() -> None:
    ticket = _ticket(
        TicketState.needs_plan, _fields(success=FieldSlot(value="settled"))
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(ticket, FieldName.success, "x", "agent")
    assert exc.value.code is ErrorCode.agent_forbidden


def test_accept_dropped_ticket_with_pending_proposal_rejected(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # A dropped ticket carrying a pending NON-gating proposal must not be acceptable:
    # pre-guard this wrote value with no state change; the guard now rejects it.
    now = fake_clock.now_unix()
    t = data.create_ticket(
        tmp_db, title="T", actor="human", now=now, title_max_chars=TITLE_MAX_CHARS
    )
    t = data.change_scope(
        tmp_db, t.id, ceiling=TicketState.needs_plan, at_cap=AtCap.propose, actor="human", now=now
    )
    t = data.file_proposal(tmp_db, t.id, field=FieldName.success, body="s", actor="agent", now=now)
    assert t.state is TicketState.needs_approach
    # plan is non-gating at needs_approach and below the ceiling: the proposal stays pending.
    t = data.file_proposal(tmp_db, t.id, field=FieldName.plan, body="plan draft", actor="agent",
                           now=now)
    assert t.state is TicketState.needs_approach
    assert t.fields.plan.proposal is not None and t.fields.plan.value is None
    t = data.drop_ticket(tmp_db, t.id, actor="human", now=now)
    assert t.state is TicketState.dropped

    count_before = len(_events(tmp_db, cfg, t.id))
    with pytest.raises(PlannerError) as exc:
        data.accept_proposal(
            tmp_db, t.id, field=FieldName.plan, actor="human", now=now,
            next_ceiling=NO_FURTHER, at_cap=AtCap.propose,
        )
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"state": "dropped"}
    t = data.read_ticket(tmp_db, t.id)
    assert t.fields.plan.value is None  # the write never landed
    assert t.fields.plan.proposal is not None
    assert len(_events(tmp_db, cfg, t.id)) == count_before
