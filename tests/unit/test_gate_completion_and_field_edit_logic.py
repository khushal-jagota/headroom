"""Pure rules for completing a user-owned gate and for editing a settled value."""

from dataclasses import replace

import pytest
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.probe import shipped_definition

from planner.core.contracts import Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets.contracts import (
    PendingTicketProposal,
    ResolvedTicketPriorityAnchors,
    Ticket,
    TicketStatus,
    WorkerStepClaim,
)
from planner.tickets.logic import resolution

PERSONAL_TASK_WORKER_TYPE_DEFINITION = shipped_definition("personal")

def _ticket(*, stage: str = "needs_success_condition") -> Ticket:
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
        ticket_status=TicketStatus.awaiting_approval,
        worker_step_claim=WorkerStepClaim.none,
        worker_step_claim_revision=0,
        conversation_id=None,
        field_values={},
        pending_proposal=None,
        created_at=0,
        updated_at=0,
    )


# Who may edit a settled value is no longer this rule's question. A settled field is in
# admission.TICKET_FIELDS_ONLY_FROM_ABOVE, so the one rule answers it where the write
# happens: test_worker_cannot_edit_settled_value, in the api file beside this one.


def test_direct_user_cannot_complete_a_gate_with_a_pending_proposal() -> None:
    ticket = replace(
        _ticket(stage="needs_outcome"),
        worker_type="personal",
        field_values={"brief": "context"},
        pending_proposal=PendingTicketProposal("outcome", "draft", "worker", 7),
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_complete_user_owned_gate(
            ticket,
            "outcome",
            "value",
            OWNER_PRINCIPAL,
            worker_type_definition=PERSONAL_TASK_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.validation


def test_direct_user_cannot_complete_a_gate_while_the_worker_step_is_out() -> None:
    ticket = replace(
        _ticket(stage="needs_outcome"),
        worker_type="personal",
        field_values={"brief": "context"},
        worker_step_claim=WorkerStepClaim.out,
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_complete_user_owned_gate(
            ticket,
            "outcome",
            "value",
            OWNER_PRINCIPAL,
            worker_type_definition=PERSONAL_TASK_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.already_running
