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

CODING_WORKER_TYPE_DEFINITION = shipped_definition("coding")
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
        worker_step_claim_changed_at=0,
        worker_step_claim_revision=0,
        conversation_id=None,
        field_values={},
        pending_proposal=None,
        created_at=0,
        updated_at=0,
    )


def test_edit_passed_value_changes_only_saved_values() -> None:
    ticket = replace(
        _ticket(stage="needs_plan"),
        field_values={"brief": "request", "success_condition": "old"},
        pending_proposal=PendingTicketProposal("plan", "draft", "worker", 9),
    )
    decision = resolution.decide_edit_settled_field(
        ticket,
        "success_condition",
        "new",
        worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
    )
    assert decision.field_values == {"brief": "request", "success_condition": "new"}
    assert decision.pending_proposal == ticket.pending_proposal
    assert decision.stage == ticket.stage


@pytest.mark.parametrize("field", ["what_changes", "plan", "bogus"])
def test_edit_settled_field_rejects_an_unsettled_or_unpassed_field(field: str) -> None:
    """Editing a value is only ever a correction. It never fills a blank."""
    ticket = replace(_ticket(stage="needs_plan"), field_values={"success_condition": "settled"})
    with pytest.raises(PlannerError) as exc:
        resolution.decide_edit_settled_field(
            ticket,
            field,
            "new",
            worker_type_definition=CODING_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.validation


# Who may edit a settled value is no longer this rule's question. A settled field is in
# admission.TICKET_FIELDS_ONLY_FROM_ABOVE, so the one rule answers it where the write
# happens: test_worker_cannot_edit_settled_value, in the api file beside this one.


def test_direct_user_completes_unset_current_user_owned_gate() -> None:
    ticket = replace(
        _ticket(stage="needs_outcome"),
        worker_type="personal",
        field_values={"brief": "context"},
        ceiling="needs_outcome",
    )

    decision = resolution.decide_complete_user_owned_gate(
        ticket,
        "outcome",
        "The result",
        OWNER_PRINCIPAL,
        worker_type_definition=PERSONAL_TASK_WORKER_TYPE_DEFINITION,
    )

    assert decision.field_values == {"brief": "context", "outcome": "The result"}
    assert decision.stage == "needs_consequences"
    assert decision.ceiling == "needs_consequences"
    assert decision.ceiling_holder == OWNER_PRINCIPAL


@pytest.mark.parametrize("field", ["brief", "consequences"])
def test_direct_user_cannot_complete_any_field_except_the_current_gate(field: str) -> None:
    ticket = replace(
        _ticket(stage="needs_outcome"),
        worker_type="personal",
        field_values={},
    )
    with pytest.raises(PlannerError) as exc:
        resolution.decide_complete_user_owned_gate(
            ticket,
            field,
            "value",
            OWNER_PRINCIPAL,
            worker_type_definition=PERSONAL_TASK_WORKER_TYPE_DEFINITION,
        )
    assert exc.value.code == ErrorCode.validation


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
