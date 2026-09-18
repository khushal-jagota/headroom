"""Pure rules for distinct pending-proposal and settled-value edits."""

from dataclasses import replace

import pytest
from tests.support.principals import OWNER_PRINCIPAL, TEST_TICKET_PRINCIPAL

from planner.core.contracts import Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import (
    AtCap,
    PendingTicketProposal,
    ResolvedTicketPriorityAnchors,
    StageOwnershipMode,
    Ticket,
    TicketStatus,
)
from planner.tickets.logic import resolution
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION


def _ticket(*, stage: str = "needs_success") -> Ticket:
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
        resolved_priority_anchors=ResolvedTicketPriorityAnchors(sprint_item=None, project=None),
        recap="",
        guidance="keep this guidance",
        ceiling="done",
        ceiling_holder=OWNER_PRINCIPAL,
        at_cap=AtCap.propose,
        ticket_status=TicketStatus.awaiting_approval,
        ticket_status_changed_at=0,
        ticket_status_revision=0,
        stage_ownership_overrides={},
        default_stage_ownership_mode=StageOwnershipMode.worker,
        effective_stage_ownership_mode=StageOwnershipMode.worker,
        conversation_id=None,
        field_values={},
        pending_proposal=None,
        archived_field_content="",
        created_at=0,
        updated_at=0,
    )


def test_edit_pending_proposal_changes_only_its_body() -> None:
    proposal = PendingTicketProposal("success", "old", "worker", 7)
    ticket = replace(_ticket(), pending_proposal=proposal, field_values={"kickoff": "request"})
    decision = resolution.decide_edit_pending_proposal(
        ticket,
        "success",
        "new",
        OWNER_PRINCIPAL,
        worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
    )
    assert decision.pending_proposal == replace(proposal, body="new")
    assert decision.field_values == {"kickoff": "request"}
    assert (decision.stage, decision.ceiling, decision.at_cap) == (
        ticket.stage,
        ticket.ceiling,
        ticket.at_cap,
    )
    assert decision.archived_field_content == ""


@pytest.mark.parametrize("field", ["approach", "bogus"])
def test_edit_pending_proposal_rejects_any_field_other_than_current_gate(field: str) -> None:
    ticket = replace(
        _ticket(), pending_proposal=PendingTicketProposal("success", "old", "worker", 7)
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_pending_proposal(
            ticket,
            field,
            "new",
            OWNER_PRINCIPAL,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.validation


def test_edit_pending_proposal_requires_a_pending_proposal() -> None:
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_pending_proposal(
            _ticket(),
            "success",
            "new",
            OWNER_PRINCIPAL,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.not_found


def test_edit_passed_value_changes_only_saved_values() -> None:
    ticket = replace(
        _ticket(stage="needs_plan"),
        field_values={"kickoff": "request", "success": "old"},
        pending_proposal=PendingTicketProposal("plan", "draft", "worker", 9),
    )
    decision = resolution.decide_edit_value(
        ticket,
        "success",
        "new",
        OWNER_PRINCIPAL,
        worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
    )
    assert decision.field_values == {"kickoff": "request", "success": "new"}
    assert decision.pending_proposal == ticket.pending_proposal
    assert decision.stage == ticket.stage


@pytest.mark.parametrize("field", ["approach", "plan", "bogus"])
def test_edit_value_rejects_unsettled_or_unpassed_field(field: str) -> None:
    ticket = replace(_ticket(stage="needs_plan"), field_values={"success": "settled"})
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(
            ticket,
            field,
            "new",
            OWNER_PRINCIPAL,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.validation


def test_worker_cannot_edit_settled_value() -> None:
    ticket = replace(_ticket(stage="needs_plan"), field_values={"success": "settled"})
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_value(
            ticket,
            "success",
            "new",
            TEST_TICKET_PRINCIPAL,
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.agent_forbidden
