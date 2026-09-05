"""Pure-logic tests for resolution.decide_edit_value.

The edit branches between role-neutral pending proposal edits and tightly guarded
direct edits of already-passed settled values. Supporting tests, no §18.3 anchor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from planner.core.contracts import EventKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    FieldSlot,
    Proposal,
    ResolvedTicketPriorityAnchors,
    StageOwnershipMode,
    Ticket,
    TicketFields,
    TicketStatus,
)
from planner.tickets.logic import fields_codec, resolution
from planner.tickets.logic.decisions import Decision
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config


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
        sprint_id=None,
        sprint_item_id=None,
        effective_sprint_id=None,
        resolved_priority_anchors=ResolvedTicketPriorityAnchors(
            sprint_item=None, project=None
        ),
        recap="",
        guidance="keep this guidance",
        ceiling=ceiling,
        at_cap=AtCap.propose,
        ticket_status=TicketStatus.empty,
        ticket_status_changed_at=0,
        ticket_status_revision=0,
        backend_error=None,
        stage_ownership_overrides={},
        default_stage_ownership_mode=StageOwnershipMode.worker,
        effective_stage_ownership_mode=StageOwnershipMode.worker,
        conversation_id=None,
        alias=None,
        fields=fields,
        created_at=0,
        updated_at=0,
    )


def _decide_edit_value(ticket: Ticket, field: str, body: str, actor: str) -> Decision:
    return resolution.decide_edit_value(
        ticket,
        field,
        body,
        actor,
        worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
    )


def _ticket_row(conn: Connection, ticket_id: str) -> tuple[object, ...]:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert row is not None
    return tuple(row)


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
    t = data.file_proposal(
        conn, t.id, field="success", body="success v1", actor="agent", now=now
    )
    t = data.file_proposal(
        conn, t.id, field="approach", body="approach v1", actor="agent", now=now
    )
    assert t.stage == "needs_plan"
    assert fields_codec.get_slot(t.fields, "success").value == "success v1"
    assert fields_codec.get_slot(t.fields, "approach").value == "approach v1"
    return t


def test_edit_passed_field_succeeds(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # Decision shape: exactly one field_value_edited event; Stage/ceiling/at_cap left None.
    ticket = _ticket(
        "needs_plan",
        _fields(success=FieldSlot(value="old success")),
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
    assert ticket.guidance == "keep this guidance"

    # End-to-end through the sole writer: value persists, Stage/ceiling untouched.
    now = fake_clock.now_unix()
    t = _passed_ticket(tmp_db, cfg, fake_clock)
    t = data.edit_field_value(
        tmp_db, t.id, field="success", new_body="success EDITED", actor="human", now=now
    )
    assert fields_codec.get_slot(t.fields, "success").value == "success EDITED"
    assert t.stage == "needs_plan"
    assert t.ceiling == "needs_plan"
    assert t.at_cap is AtCap.propose


def test_edit_unset_value_rejected() -> None:
    ticket = _ticket("needs_approach", _fields(success=FieldSlot(value=None)))
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "success", "x", "human")
    assert exc.value.code is ErrorCode.validation
    assert exc.value.detail == {"field": "success"}


@pytest.mark.parametrize(
    "actor", ["human", "agent", "worker", "sprint_item_supervisor"]
)
def test_edit_pending_proposal_preserves_its_state_for_every_actor(actor: str) -> None:
    ticket = _ticket(
        "needs_plan",
        _fields(
            success=FieldSlot(
                value="settled",
                proposal=Proposal(
                    body="pending", proposed_by="original-worker", created_at=17
                ),
            )
        ),
        ceiling="needs_plan",
    )
    decision = _decide_edit_value(ticket, "success", "edited proposal", actor)

    assert decision.new_stage is None
    assert decision.new_ceiling is None
    assert decision.new_at_cap is None
    assert len(decision.events) == 1
    assert decision.events[0].kind is EventKind.proposal_edited
    assert decision.events[0].payload == {
        "field": "success",
        "body": "edited proposal",
    }
    assert decision.new_fields is not None
    slot = fields_codec.get_slot(decision.new_fields, "success")
    assert slot.value == "settled"
    assert ticket.guidance == "keep this guidance"
    assert slot.proposal == Proposal(
        body="edited proposal", proposed_by="original-worker", created_at=17
    )


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


def test_edit_agent_actor_forbidden() -> None:
    ticket = _ticket("needs_plan", _fields(success=FieldSlot(value="settled")))
    with pytest.raises(PlannerError) as exc:
        _decide_edit_value(ticket, "success", "x", "agent")
    assert exc.value.code is ErrorCode.agent_forbidden


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
        "UPDATE tickets SET ticket_status = 'paired' WHERE id = ?",
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
    assert ticket.ticket_status is TicketStatus.paired
    assert fields_codec.get_slot(ticket.fields, "plan").value == "plan draft"
