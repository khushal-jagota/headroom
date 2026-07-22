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
    FieldSlot,
    Proposal,
    StageOwnershipMode,
    Ticket,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic import fields_codec, resolution
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config
    from planner.core.contracts import EventRow


def _fields(**slots: FieldSlot) -> TicketFields:
    ids = ("kickoff", "success", "approach", "plan", "implementation", "closeout")
    return TicketFields({fid: slots.get(fid, FieldSlot()) for fid in ids})


def _ticket(stage: str, fields: TicketFields, *, ceiling: str = "done") -> Ticket:
    return Ticket(
        id="t_test",
        title="T",
        worker_type="coding",
        employee_backend="hermes",
        stage=stage,
        priority=Priority.P3,
        deadline=None,
        project_id=None,
        project_name=None,
        sprint_item_id=None,
        sprint_id=None,
        recap="",
        ceiling=ceiling,
        at_cap=AtCap.propose,
        ticket_status=TicketStatus.empty,
        backend_error=None,
        stage_ownership_overrides={},
        default_stage_ownership_mode=StageOwnershipMode.worker,
        effective_stage_ownership_mode=StageOwnershipMode.worker,
        employee_session_id=None,
        alias=None,
        fields=fields,
        created_at=0,
        updated_at=0,
    )


def _decide_edit_value(ticket: Ticket, field: str, body: str, actor: str):
    return resolution.decide_edit_value(
        ticket,
        field,
        body,
        actor,
        worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
    )


def _events(
    conn: Connection, cfg: Config, ticket_id: str, kind: EventKind | None = None
) -> list[EventRow]:
    rows = read_events_since(conn, 0, cfg.events_read_limit)
    return [e for e in rows if e.entity_id == ticket_id and (kind is None or e.kind == kind.value)]


def _passed_ticket(conn: Connection, cfg: Config, clock: TestClock) -> Ticket:
    """A real ticket driven to needs_plan with success & approach settled (values),
    plan the current gating field (unset)."""
    now = clock.now_unix()
    t = data.create_ticket(
        conn,
        worker_type="coding",
        title="T",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
    )
    t = data.accept_proposal(
        conn,
        t.id,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    t = data.change_scope(
        conn, t.id, ceiling="needs_plan", at_cap=AtCap.propose, actor="human", now=now
    )
    t = data.file_proposal(conn, t.id, field="success", body="success v1", actor="agent", now=now)
    t = data.file_proposal(conn, t.id, field="approach", body="approach v1", actor="agent", now=now)
    assert t.stage == "needs_plan"
    assert fields_codec.get_slot(t.fields, "success").value == "success v1"
    assert fields_codec.get_slot(t.fields, "approach").value == "approach v1"
    return t


def test_edit_passed_field_succeeds(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    # Decision shape: exactly one field_value_edited event; Stage/ceiling/at_cap left None.
    ticket = _ticket(
        "needs_plan",
        _fields(success=FieldSlot(value="old success", user_note="keep me")),
        ceiling="needs_plan",
    )
    decision = _decide_edit_value(ticket, "success", "new success", "human")
    assert decision.new_stage is None
    assert decision.new_ceiling is None
    assert decision.new_at_cap is None
    assert len(decision.events) == 1
    assert decision.events[0].kind is EventKind.field_value_edited
    assert decision.events[0].payload == {"field": "success", "body": "new success"}
    assert decision.new_fields is not None
    edited_slot = fields_codec.get_slot(decision.new_fields, "success")
    assert edited_slot.value == "new success"
    assert edited_slot.user_note == "keep me"  # user note preserved

    # End-to-end through the sole appender: value persists, Stage/ceiling untouched,
    # one field_value_edited row logged.
    now = fake_clock.now_unix()
    t = _passed_ticket(tmp_db, cfg, fake_clock)
    t = data.edit_field_value(
        tmp_db, t.id, field="success", new_body="success EDITED", actor="human", now=now
    )
    assert fields_codec.get_slot(t.fields, "success").value == "success EDITED"
    assert t.stage == "needs_plan"
    assert t.ceiling == "needs_plan"
    assert t.at_cap is AtCap.propose
    logged = _events(tmp_db, cfg, t.id, EventKind.field_value_edited)
    assert len(logged) == 1
    assert logged[0].payload == {"field": "success", "body": "success EDITED"}


def test_edit_unset_value_rejected() -> None:
    ticket = _ticket("needs_approach", _fields(success=FieldSlot(value=None)))
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "success", "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "success"}


def test_edit_field_with_live_proposal_rejected() -> None:
    ticket = _ticket(
        "needs_plan",
        _fields(
            success=FieldSlot(
                value="settled",
                proposal=Proposal(body="pending", proposed_by="agent", created_at=0),
            )
        ),
    )
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "success", "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "success"}


def test_edit_current_gating_field_rejected() -> None:
    # plan gates needs_plan; at needs_plan it is not yet passed even with a settled value.
    ticket = _ticket(
        "needs_plan",
        _fields(plan=FieldSlot(value="plan value")),
        ceiling="needs_plan",
    )
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "plan", "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "plan", "stage": "needs_plan"}


def test_edit_future_field_with_value_rejected() -> None:
    # Backward-Stage-jump hazard: stage=needs_approach but plan already holds a value.
    ticket = _ticket(
        "needs_approach",
        _fields(plan=FieldSlot(value="plan value")),
    )
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "plan", "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "plan", "stage": "needs_approach"}


def test_edit_dropped_ticket_rejected() -> None:
    ticket = _ticket("dropped", _fields(success=FieldSlot(value="settled")))
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "success", "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"stage": "dropped"}


def test_edit_agent_actor_forbidden() -> None:
    ticket = _ticket("needs_plan", _fields(success=FieldSlot(value="settled")))
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "success", "x", "agent")
    assert exc.value.code is ErrorCode.agent_forbidden


def test_accept_dropped_ticket_with_pending_proposal_rejected(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # A dropped ticket carrying a pending NON-gating proposal must not be acceptable:
    # pre-guard this wrote value with no Stage change; the guard now rejects it.
    now = fake_clock.now_unix()
    t = data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="T",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
    )
    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    t = data.change_scope(
        tmp_db, t.id, ceiling="needs_plan", at_cap=AtCap.propose, actor="human", now=now
    )
    t = data.file_proposal(tmp_db, t.id, field="success", body="s", actor="agent", now=now)
    assert t.stage == "needs_approach"
    # plan is non-gating at needs_approach and below the ceiling: the proposal stays pending.
    t = data.file_proposal(tmp_db, t.id, field="plan", body="plan draft", actor="agent", now=now)
    assert t.stage == "needs_approach"
    plan_slot = fields_codec.get_slot(t.fields, "plan")
    assert plan_slot.proposal is not None and plan_slot.value is None
    t = data.drop_ticket(tmp_db, t.id, actor="human", now=now)
    assert t.stage == "dropped"

    count_before = len(_events(tmp_db, cfg, t.id))
    with pytest.raises(PlannerError) as exc:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="plan",
            actor="human",
            now=now,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"stage": "dropped"}
    t = data.read_ticket(tmp_db, t.id)
    assert fields_codec.get_slot(t.fields, "plan").value is None  # the write never landed
    assert fields_codec.get_slot(t.fields, "plan").proposal is not None
    assert len(_events(tmp_db, cfg, t.id)) == count_before


def test_accept_non_gating_proposal_keeps_opened_paired_stage_resting(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    ticket = data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Paired design",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
    )
    ticket = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="kickoff",
        actor="human",
        now=now,
        next_ceiling="needs_plan",
        at_cap=AtCap.propose,
    )
    ticket = data.file_proposal(
        tmp_db,
        ticket.id,
        field="success",
        body="success",
        actor="agent",
        now=now,
    )
    assert ticket.stage == "needs_approach"
    ticket = data.set_stage_ownership(
        tmp_db,
        ticket.id,
        stage="needs_approach",
        ownership_mode=StageOwnershipMode.paired,
        now=now,
    )
    assert ticket.ticket_status is TicketStatus.empty
    tmp_db.execute(
        "UPDATE tickets SET ticket_status = 'paired_work' WHERE id = ?",
        (ticket.id,),
    )

    ticket = data.file_proposal(
        tmp_db,
        ticket.id,
        field="plan",
        body="plan draft",
        actor="agent",
        now=now,
    )
    assert ticket.stage == "needs_approach"
    assert ticket.ticket_status is TicketStatus.awaiting_approval

    ticket = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="plan",
        actor="human",
        now=now,
    )
    assert ticket.stage == "needs_approach"
    assert ticket.ticket_status is TicketStatus.paired_work
    assert fields_codec.get_slot(ticket.fields, "plan").value == "plan draft"
