"""Pure rules for completing a user-owned gate and for editing a settled value."""


from tests.support.principals import OWNER_PRINCIPAL
from tests.support.retired_workers import historical_definition

from planner.tickets.contracts import (
    Ticket,
    TicketStatus,
    WorkerStepClaim,
)

PERSONAL_TASK_WORKER_TYPE_DEFINITION = historical_definition("personal")


def _ticket(*, stage: str = "needs_success_condition") -> Ticket:
    return Ticket(
        id="t_test",
        title="T",
        worker_type="coding",
        employee_backend="hermes",
        stage=stage,
        deadline=None,
        project_id=None,
        project_name=None,
        sprint_id=None,
        sprint_item_id=None,
        effective_sprint_id=None,
        sprint_item_title=None,
        recap="",
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
