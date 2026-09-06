"""Pure decisions over the Ticket's current result and saved values."""

from __future__ import annotations

from dataclasses import replace

from planner.core.contracts import ErrorCode, PlannerError
from planner.tickets.contracts import (
    AtCap,
    NextCeiling,
    PendingTicketProposal,
    ScopePair,
    Ticket,
    TicketStatus,
)
from planner.tickets.logic import admission, archive, fields_codec, machine
from planner.tickets.logic.decisions import Decision
from planner.worker_types.contracts import WorkerTypeDefinition


def _accept_gating_proposal(
    ticket: Ticket,
    field: str,
    stored_body: str,
    scope: ScopePair | None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    target = worker_type_definition.advance_target(ticket.stage)
    if target is None:
        raise PlannerError(ErrorCode.validation, "stage has no advance target")
    return replace(
        Decision.from_ticket(ticket),
        field_values={**ticket.field_values, field: stored_body},
        pending_proposal=None,
        stage=target,
        ceiling=ticket.ceiling if scope is None else scope.next_ceiling,
        at_cap=ticket.at_cap if scope is None else scope.at_cap,
    )


def decide_file_proposal(
    ticket: Ticket, body: str, actor: str, now: int, *, worker_type_definition: WorkerTypeDefinition
) -> Decision:
    admission.validate_body(body, "proposal body")
    field = worker_type_definition.gating_field(ticket.stage)
    if field is None:
        raise PlannerError(ErrorCode.validation, "ticket stage has no proposal field")
    admission.check_agent_proposal(
        ticket.stage,
        ticket.ceiling,
        ticket.at_cap,
        field,
        worker_type_definition=worker_type_definition,
    )
    ownership = machine.effective_stage_ownership_mode(
        ticket.stage,
        ticket.stage_ownership_overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=ticket.default_stage_ownership_mode,
    )
    if (
        ownership is not None
        and ownership.value == "worker"
        and machine.auto_accept_target(
            ticket.stage, ticket.ceiling, field, worker_type_definition=worker_type_definition
        )
        is not None
    ):
        return _accept_gating_proposal(
            ticket, field, body, None, worker_type_definition=worker_type_definition
        )
    return replace(
        Decision.from_ticket(ticket),
        pending_proposal=PendingTicketProposal(field, body, actor, now),
    )


def _pending(ticket: Ticket, field: str, definition: WorkerTypeDefinition) -> PendingTicketProposal:
    proposal = ticket.pending_proposal
    if proposal is None:
        raise PlannerError(ErrorCode.not_found, "no pending proposal", {"ticket_id": ticket.id})
    if field != proposal.field or field != definition.gating_field(ticket.stage):
        raise PlannerError(
            ErrorCode.validation, "proposal field is not the current gate", {"field": field}
        )
    return proposal


def decide_accept(
    ticket: Ticket,
    field: str,
    actor: str,
    edited_body: str | None,
    next_ceiling: NextCeiling | None,
    at_cap: AtCap | None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    admission.require_direct_or_supervisor_actor(actor, "accept_proposal")
    if edited_body is not None:
        admission.validate_body(edited_body, "edit-accept text")
    proposal = _pending(ticket, field, worker_type_definition)
    target = worker_type_definition.advance_target(ticket.stage)
    if target is None:
        raise PlannerError(ErrorCode.validation, "stage has no advance target")
    scope = machine.resolve_scope(
        target, next_ceiling, at_cap, worker_type_definition=worker_type_definition
    )
    return _accept_gating_proposal(
        ticket,
        field,
        proposal.body if edited_body is None else edited_body,
        scope,
        worker_type_definition=worker_type_definition,
    )


def decide_edit_pending_proposal(
    ticket: Ticket,
    field: str,
    new_body: str,
    actor: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    admission.validate_body(new_body, "proposal body")
    proposal = _pending(ticket, field, worker_type_definition)
    return replace(Decision.from_ticket(ticket), pending_proposal=replace(proposal, body=new_body))


def decide_edit_value(
    ticket: Ticket,
    field: str,
    new_body: str,
    actor: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    admission.validate_body(new_body, "field value")
    admission.require_direct_actor(actor, "edit_field_value")
    if ticket.stage == "dropped":
        raise PlannerError(ErrorCode.validation, "dropped tickets cannot be edited")
    if (
        fields_codec.field_value(
            ticket.field_values, field, worker_type_definition=worker_type_definition
        )
        is None
    ):
        raise PlannerError(
            ErrorCode.validation, "field has no settled value to edit", {"field": field}
        )
    if not machine.field_is_passed(
        field, ticket.stage, worker_type_definition=worker_type_definition
    ):
        raise PlannerError(ErrorCode.validation, "field is not yet passed", {"field": field})
    return replace(
        Decision.from_ticket(ticket), field_values={**ticket.field_values, field: new_body}
    )


def decide_return_for_revision(
    ticket: Ticket, actor: str, *, worker_type_definition: WorkerTypeDefinition
) -> Decision:
    admission.require_direct_or_supervisor_actor(actor, "return_for_revision")
    if ticket.ticket_status is TicketStatus.agent:
        raise PlannerError(
            ErrorCode.already_running, "the ticket worker is already revising this proposal"
        )
    if worker_type_definition.is_terminal(ticket.stage):
        raise PlannerError(ErrorCode.validation, "terminal tickets cannot be returned for revision")
    if ticket.conversation_id is None:
        raise PlannerError(ErrorCode.validation, "ticket has no existing worker session")
    field = worker_type_definition.gating_field(ticket.stage)
    if field is None:
        raise PlannerError(ErrorCode.validation, "ticket has no approval item to return")
    _pending(ticket, field, worker_type_definition)
    return replace(Decision.from_ticket(ticket), pending_proposal=None)


def decide_drop(ticket: Ticket, actor: str) -> Decision:
    admission.require_direct_actor(actor, "drop_ticket")
    if ticket.stage in ("done", "dropped"):
        raise PlannerError(
            ErrorCode.validation, "terminal tickets cannot be dropped", {"stage": ticket.stage}
        )
    record = ticket.archived_field_content
    if ticket.pending_proposal is not None:
        record = "\n\n".join(
            part for part in (record, archive.archived_proposal(ticket.pending_proposal)) if part
        )
    return replace(
        Decision.from_ticket(ticket),
        stage="dropped",
        pending_proposal=None,
        archived_field_content=record,
    )


def decide_scope_change(
    ticket: Ticket,
    ceiling: str,
    at_cap: AtCap,
    actor: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    admission.require_direct_or_supervisor_actor(actor, "change_scope")
    return replace(
        Decision.from_ticket(ticket),
        ceiling=worker_type_definition.resolve_ceiling(ceiling),
        at_cap=at_cap,
    )
