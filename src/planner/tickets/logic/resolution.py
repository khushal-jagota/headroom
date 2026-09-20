"""Pure decisions over the Ticket's current result and saved values."""

from __future__ import annotations

from dataclasses import replace

from planner.core.contracts import (
    OWNER_PRINCIPAL,
    ErrorCode,
    PlannerError,
    Principal,
    principal_legacy_actor,
)
from planner.tickets.contracts import (
    NextCeiling,
    PendingTicketProposal,
    StageOwnershipMode,
    Ticket,
    WorkerStepClaim,
)
from planner.tickets.logic import admission, fields_codec, machine
from planner.tickets.logic.decisions import Decision
from planner.worker_types.contracts import WorkerTypeDefinition


def _accept_gating_proposal(
    ticket: Ticket,
    field: str,
    stored_body: str,
    next_ceiling: str | None,
    next_holder: Principal | None = None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    target = worker_type_definition.advance_target(ticket.stage)
    if target is None:
        raise PlannerError(ErrorCode.validation, "stage has no advance target")
    ceiling_holder = ticket.ceiling_holder
    if next_ceiling is not None:
        assert next_holder is not None
        ceiling_holder = next_holder
    return replace(
        Decision.from_ticket(ticket),
        field_values={**ticket.field_values, field: stored_body},
        pending_proposal=None,
        stage=target,
        ceiling=ticket.ceiling if next_ceiling is None else next_ceiling,
        ceiling_holder=ceiling_holder,
    )


def decide_file_proposal(
    ticket: Ticket,
    body: str,
    principal: Principal,
    now: int,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    """What a worker's answer to the current Stage becomes.

    Below the ceiling the answer is the Stage's value: it settles, the Stage advances, and
    no proposal is ever recorded. At the ceiling the answer is a proposal, and the Ticket
    parks until somebody decides it. A ceiling is the only thing that makes a proposal, so
    every proposal in the system is one a person is going to look at.

    A user-owned Stage parks whatever the ceiling says. Its worker is there to talk, not
    to settle the Stage on its own.
    """
    admission.validate_body(body, "proposal body")
    field = worker_type_definition.gating_field(ticket.stage)
    if field is None:
        raise PlannerError(ErrorCode.validation, "ticket stage has no proposal field")
    admission.check_agent_proposal(
        ticket.stage,
        field,
        worker_type_definition=worker_type_definition,
    )
    ownership = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    below_the_ceiling = not machine.at_or_beyond_ceiling(
        ticket.stage,
        ticket.ceiling,
        worker_type_definition=worker_type_definition,
    )
    if ownership is StageOwnershipMode.worker and below_the_ceiling:
        return _accept_gating_proposal(
            ticket, field, body, None, worker_type_definition=worker_type_definition
        )
    return replace(
        Decision.from_ticket(ticket),
        pending_proposal=PendingTicketProposal(
            field, body, principal_legacy_actor(principal), now
        ),
    )


def _pending(
    ticket: Ticket, field: str, definition: WorkerTypeDefinition
) -> PendingTicketProposal:
    proposal = ticket.pending_proposal
    if proposal is None:
        raise PlannerError(
            ErrorCode.not_found, "no pending proposal", {"ticket_id": ticket.id}
        )
    if field != proposal.field or field != definition.gating_field(ticket.stage):
        raise PlannerError(
            ErrorCode.validation,
            "proposal field is not the current gate",
            {"field": field},
        )
    return proposal


def decide_accept(
    ticket: Ticket,
    field: str,
    principal: Principal,
    edited_body: str | None,
    next_ceiling: NextCeiling | None,
    next_holder: Principal | None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    if edited_body is not None:
        admission.validate_body(edited_body, "edit-accept text")
    proposal = _pending(ticket, field, worker_type_definition)
    target = worker_type_definition.advance_target(ticket.stage)
    if target is None:
        raise PlannerError(ErrorCode.validation, "stage has no advance target")
    resolved_ceiling = machine.resolve_next_ceiling(
        target, next_ceiling, worker_type_definition=worker_type_definition
    )
    if next_holder is None:
        raise PlannerError(
            ErrorCode.scope_missing,
            "approval requires the holder for the next ceiling",
            {"missing": ["next_holder"]},
        )
    return _accept_gating_proposal(
        ticket,
        field,
        proposal.body if edited_body is None else edited_body,
        resolved_ceiling,
        next_holder,
        worker_type_definition=worker_type_definition,
    )


def decide_complete_user_owned_gate(
    ticket: Ticket,
    field: str,
    new_body: str,
    principal: Principal,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    """The user does a user-owned Stage's work themselves, and the Stage advances.

    This is not a field edit, which is why it is not one. It settles a blank the Ticket
    is currently gated on and moves the Ticket forward, so it is an operation with the
    same consequence an approval has, reached the only other way a Stage can be filled.
    Correcting an already-settled value is the ordinary edit, on the ordinary path.
    """
    admission.validate_body(new_body, "field value")
    if worker_type_definition.is_terminal(ticket.stage):
        raise PlannerError(
            ErrorCode.validation, "terminal tickets have no gate to complete", {"field": field}
        )
    settled_value = fields_codec.field_value(
        ticket.field_values, field, worker_type_definition=worker_type_definition
    )
    if settled_value is not None:
        raise PlannerError(
            ErrorCode.validation, "field is already settled", {"field": field}
        )
    gating_field = worker_type_definition.gating_field(ticket.stage)
    if field != gating_field:
        raise PlannerError(
            ErrorCode.validation,
            "only the current user-owned gate can be completed",
            {"field": field, "gating_field": gating_field, "stage": ticket.stage},
        )
    ownership = machine.stage_ownership_mode(
        ticket.stage,
        worker_type_definition=worker_type_definition,
    )
    if ownership is not StageOwnershipMode.user:
        raise PlannerError(
            ErrorCode.agent_forbidden,
            "only a user-owned stage can be completed with a direct value",
            {"field": field, "stage": ticket.stage},
        )
    if ticket.pending_proposal is not None:
        raise PlannerError(
            ErrorCode.validation,
            "a pending proposal must be resolved before direct completion",
            {"field": field},
        )
    if ticket.worker_step_claim is WorkerStepClaim.out:
        raise PlannerError(
            ErrorCode.already_running,
            "ticket control is active",
            {"ticket_id": ticket.id, "worker_step_claim": ticket.worker_step_claim.value},
        )
    target = worker_type_definition.advance_target(ticket.stage)
    if target is None:
        raise PlannerError(ErrorCode.validation, "stage has no advance target")
    return _accept_gating_proposal(
        ticket,
        field,
        new_body,
        target,
        principal,
        worker_type_definition=worker_type_definition,
    )


def decide_edit_settled_field(
    ticket: Ticket,
    field: str,
    new_body: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    """Correct a value the Ticket has already passed. It changes nothing else."""
    admission.validate_body(new_body, "field value")
    if (
        fields_codec.field_value(
            ticket.field_values, field, worker_type_definition=worker_type_definition
        )
        is None
    ):
        raise PlannerError(
            ErrorCode.validation,
            "field has no settled value to edit; a Stage still waiting for its answer is "
            "completed, not edited",
            {"field": field},
        )
    if not machine.field_is_passed(
        field, ticket.stage, worker_type_definition=worker_type_definition
    ):
        raise PlannerError(
            ErrorCode.validation, "field is not yet passed", {"field": field}
        )
    return replace(
        Decision.from_ticket(ticket),
        field_values={**ticket.field_values, field: new_body},
    )


def decide_reject(
    ticket: Ticket,
    principal: Principal,
    *,
    has_guidance: bool,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    """Send a parked proposal back. The proposal goes; the Stage does not move.

    Guidance is optional, so the refusal for a Ticket with no worker conversation only
    applies when there is guidance to deliver into one.
    """
    if ticket.worker_step_claim is WorkerStepClaim.out:
        raise PlannerError(
            ErrorCode.already_running,
            "the ticket worker is already revising this proposal",
        )
    if worker_type_definition.is_terminal(ticket.stage):
        raise PlannerError(ErrorCode.validation, "terminal tickets cannot be rejected")
    if has_guidance and ticket.conversation_id is None:
        raise PlannerError(
            ErrorCode.validation, "ticket has no existing worker session"
        )
    field = worker_type_definition.gating_field(ticket.stage)
    if field is None:
        raise PlannerError(
            ErrorCode.validation, "ticket has no approval item to reject"
        )
    _pending(ticket, field, worker_type_definition)
    return replace(
        Decision.from_ticket(ticket),
        pending_proposal=None,
        ceiling_holder=(
            OWNER_PRINCIPAL if principal == OWNER_PRINCIPAL else ticket.ceiling_holder
        ),
    )


def decide_set_ceiling(
    ticket: Ticket,
    ceiling: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    if ticket.pending_proposal is not None:
        raise PlannerError(
            ErrorCode.validation,
            "the ceiling stage cannot change while a proposal is pending",
            {"ticket_id": ticket.id},
        )
    # The holder is not implied by who moved the ceiling. Raising or lowering how far a
    # Ticket may go must never move it into a different approval queue.
    return replace(
        Decision.from_ticket(ticket),
        ceiling=worker_type_definition.resolve_ceiling(ceiling),
    )


def decide_set_ceiling_holder(ticket: Ticket, holder: Principal) -> Decision:
    """Re-address a parked proposal: say which principal it is now for.

    The holder is an address. It says who a proposal is for, and it decides nothing about
    who may accept or reject it. So handing it on is an ordinary write on the Ticket, and
    the caller needs what any other write on the Ticket needs: to stand above it.

    It is still set on its own, separately from the ceiling stage. A filed proposal
    freezes how far the Ticket may go; who is asked about it is not frozen, because
    re-addressing changes nothing about what was proposed.
    """
    return replace(Decision.from_ticket(ticket), ceiling_holder=holder)
