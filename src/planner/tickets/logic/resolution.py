"""§4.4 resolution semantics as pure Decisions. _accept_gating_proposal is the
sole constructor of any Decision that traverses the gating-acceptance edges 1-5
(auto and direct alike); _stage_change is the sole constructor of stage_changed
EventSpecs. Together with data._apply_decision (the sole appender) this is the
"one canonical writer per transition" guarantee (§4.4.6)."""

from __future__ import annotations

from typing import Final

from planner.core.contracts import ErrorCode, EventKind, PlannerError
from planner.tickets.contracts import (
    AtCap,
    FieldSlot,
    NextCeiling,
    Proposal,
    ScopePair,
    Ticket,
    TicketStatus,
)
from planner.tickets.logic import admission, fields_codec, machine
from planner.tickets.logic.decisions import Decision, EventSpec
from planner.worker_types.contracts import WorkerTypeDefinition

CAUSE_AUTO_ACCEPT: Final[str] = "auto_accept"
CAUSE_DIRECT_ACCEPT: Final[str] = "direct_accept"
CAUSE_DIRECT_STAGE_JUMP: Final[str] = "direct_stage_jump"
CAUSE_DROP: Final[str] = "drop"
CAUSE_ONWARD_SCOPE: Final[str] = "onward_scope"
CAUSE_DIRECT_SCOPE: Final[str] = "direct_scope"
RESOLVED_BY_AUTO: Final[str] = "auto"
RESOLVED_BY_DIRECT: Final[str] = "direct"


def _stage_change(old: str, new: str, cause: str) -> EventSpec:
    return EventSpec(
        EventKind.stage_changed, {"from_stage": str(old), "to_stage": str(new), "cause": cause}
    )


def _accept_gating_proposal(
    ticket: Ticket,
    field: str,
    stored_body: str,
    resolved_by: str,
    edited: bool,
    scope: ScopePair | None,
    cause: str,
    superseded_body: str | None = None,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    slot = fields_codec.get_slot(ticket.fields, str(field))
    new_slot = FieldSlot(value=stored_body, proposal=None, user_note=slot.user_note)
    new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
    new_stage = worker_type_definition.advance_target(ticket.stage)
    if new_stage is None:
        raise PlannerError(
            ErrorCode.validation,
            "stage has no advance target",
            {"stage": ticket.stage},
        )
    events: list[EventSpec] = []
    if superseded_body is not None:
        events.append(
            EventSpec(
                EventKind.proposal_superseded,
                {"field": str(field), "replaced_body": superseded_body},
            )
        )
    events.append(
        EventSpec(
            EventKind.proposal_accepted,
            {
                "field": str(field),
                "body": stored_body,
                "resolved_by": resolved_by,
                "edited": edited,
            },
        )
    )
    events.append(_stage_change(ticket.stage, new_stage, cause))
    new_ceiling: str | None = None
    new_at_cap: AtCap | None = None
    if scope is not None:
        new_ceiling = scope.next_ceiling  # already a resolved ceiling id (str)
        new_at_cap = scope.at_cap
        events.append(
            EventSpec(
                EventKind.scope_changed,
                {
                    "ceiling": scope.next_ceiling,
                    "at_cap": scope.at_cap.value,
                    "cause": CAUSE_ONWARD_SCOPE,
                },
            )
        )
    return Decision(
        events=tuple(events),
        new_fields=new_fields,
        new_stage=new_stage,
        new_ceiling=new_ceiling,
        new_at_cap=new_at_cap,
    )


def decide_file_proposal(
    ticket: Ticket,
    field: str,
    body: str,
    actor: str,
    now: int,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    admission.validate_body(body, "proposal body")
    admission.check_agent_proposal(
        ticket.stage,
        ticket.ceiling,
        ticket.at_cap,
        field,
        worker_type_definition=worker_type_definition,
    )
    slot = fields_codec.get_slot(ticket.fields, str(field))
    superseded_body = slot.proposal.body if slot.proposal is not None else None
    ownership_mode = machine.effective_stage_ownership_mode(
        ticket.stage,
        ticket.stage_ownership_overrides,
        worker_type_definition=worker_type_definition,
        default_stage_ownership_mode=ticket.default_stage_ownership_mode,
    )
    if (
        ownership_mode is not None
        and ownership_mode.value == "worker"
        and machine.auto_accept_target(
            ticket.stage,
            ticket.ceiling,
            field,
            worker_type_definition=worker_type_definition,
        )
        is not None
    ):
        return _accept_gating_proposal(
            ticket,
            field,
            stored_body=body,
            resolved_by=RESOLVED_BY_AUTO,
            edited=False,
            scope=None,
            cause=CAUSE_AUTO_ACCEPT,
            superseded_body=superseded_body,
            worker_type_definition=worker_type_definition,
        )
    new_slot = FieldSlot(
        value=slot.value,
        proposal=Proposal(body=body, proposed_by=actor, created_at=now),
        user_note=slot.user_note,
    )
    new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
    events: list[EventSpec] = []
    if superseded_body is not None:
        events.append(
            EventSpec(
                EventKind.proposal_superseded,
                {"field": str(field), "replaced_body": superseded_body},
            )
        )
    events.append(
        EventSpec(
            EventKind.proposal_filed,
            {"field": str(field), "body": body, "proposed_by": actor},
        )
    )
    return Decision(events=tuple(events), new_fields=new_fields)


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
    admission.require_direct_actor(actor, "accept_proposal")
    if edited_body is not None:
        admission.validate_body(edited_body, "edit-accept text")
    edited = edited_body is not None
    if ticket.stage == "dropped":
        raise PlannerError(
            ErrorCode.validation, "dropped tickets cannot be accepted", {"stage": "dropped"}
        )
    slot = fields_codec.get_slot(ticket.fields, str(field))
    if slot.proposal is None:
        raise PlannerError(
            ErrorCode.not_found,
            "no pending proposal on field",
            {"ticket_id": ticket.id, "field": str(field)},
        )
    stored_body = edited_body if edited_body is not None else slot.proposal.body
    if field == worker_type_definition.gating_field(ticket.stage):
        new_stage = worker_type_definition.advance_target(ticket.stage)
        if new_stage is None:
            raise PlannerError(
                ErrorCode.validation,
                "stage has no advance target",
                {"stage": ticket.stage},
            )
        scope = machine.resolve_scope(
            new_stage,
            next_ceiling,
            at_cap,
            worker_type_definition=worker_type_definition,
        )
        return _accept_gating_proposal(
            ticket,
            field,
            stored_body=stored_body,
            resolved_by=RESOLVED_BY_DIRECT,
            edited=edited,
            scope=scope,
            cause=CAUSE_DIRECT_ACCEPT,
            worker_type_definition=worker_type_definition,
        )
    new_slot = FieldSlot(value=stored_body, proposal=None, user_note=slot.user_note)
    new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
    events = (
        EventSpec(
            EventKind.proposal_accepted,
            {
                "field": str(field),
                "body": stored_body,
                "resolved_by": RESOLVED_BY_DIRECT,
                "edited": edited,
            },
        ),
    )
    return Decision(events=events, new_fields=new_fields)


def decide_edit_value(
    ticket: Ticket,
    field: str,
    new_body: str,
    actor: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    """§4.2 direct edit of an already-*passed* settled value. The value stays written
    solely by the proposal resolver; this is a tightly-guarded direct write path that
    never touches stage/ceiling. It rejects dropped tickets, an unset value, a field
    carrying a live proposal, and the current gating or any future field."""
    admission.require_direct_actor(actor, "edit_field_value")
    admission.validate_body(new_body, "field value")
    if ticket.stage == "dropped":
        raise PlannerError(
            ErrorCode.validation, "dropped tickets cannot be edited", {"stage": "dropped"}
        )
    slot = fields_codec.get_slot(ticket.fields, str(field))
    if slot.value is None:
        raise PlannerError(
            ErrorCode.validation, "field has no settled value to edit", {"field": str(field)}
        )
    if slot.proposal is not None:
        raise PlannerError(
            ErrorCode.validation, "field has a pending proposal", {"field": str(field)}
        )
    if not machine.field_is_passed(
        field,
        ticket.stage,
        worker_type_definition=worker_type_definition,
    ):
        raise PlannerError(
            ErrorCode.validation,
            "field is not yet passed",
            {"field": str(field), "stage": str(ticket.stage)},
        )
    new_slot = FieldSlot(value=new_body, proposal=None, user_note=slot.user_note)
    new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
    return Decision(
        events=(EventSpec(EventKind.field_value_edited, {"field": str(field), "body": new_body}),),
        new_fields=new_fields,
    )


def decide_return_for_revision(
    ticket: Ticket,
    actor: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    admission.require_direct_actor(actor, "return_for_revision")
    if ticket.ticket_status is TicketStatus.agent:
        raise PlannerError(
            ErrorCode.already_running,
            "the ticket worker is already revising this proposal",
            {"ticket_id": ticket.id},
        )
    if ticket.stage in ("done", "dropped"):
        raise PlannerError(
            ErrorCode.validation,
            "terminal tickets cannot be returned for revision",
            {"stage": str(ticket.stage)},
        )
    if ticket.employee_session_id is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket has no existing worker session",
            {"ticket_id": ticket.id},
        )
    field = worker_type_definition.gating_field(ticket.stage)
    if field is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket has no approval item to return",
            {"stage": str(ticket.stage)},
        )
    slot = fields_codec.get_slot(ticket.fields, str(field))
    if slot.proposal is None:
        raise PlannerError(
            ErrorCode.not_found,
            "no pending proposal to return",
            {"ticket_id": ticket.id, "field": str(field)},
        )
    new_slot = FieldSlot(value=slot.value, proposal=None, user_note=slot.user_note)
    return Decision(
        events=(), new_fields=fields_codec.with_slot(ticket.fields, str(field), new_slot)
    )


def decide_stage_jump(ticket: Ticket, new_stage: str, actor: str) -> Decision:
    admission.require_direct_actor(actor, "set_stage")
    # The reserved bookends (dropped, needs_kickoff) are string-identical for every
    # type; the ingress has already validated new_stage is a linear stage of the
    # ticket's type (or is dropped), so guarding on the bookend strings is type-safe.
    # No definition is needed: only the universal bookends and the equality guard run.
    if new_stage == "dropped":
        raise PlannerError(ErrorCode.validation, "use the drop action")
    if ticket.stage == "dropped":
        raise PlannerError(ErrorCode.validation, "dropped is terminal")
    if ticket.stage == "needs_kickoff" or new_stage == "needs_kickoff":
        raise PlannerError(
            ErrorCode.validation,
            "kickoff stage changes only through kickoff approval",
        )
    if str(new_stage) == str(ticket.stage):
        raise PlannerError(ErrorCode.validation, "ticket already in that stage")
    events = (_stage_change(ticket.stage, new_stage, CAUSE_DIRECT_STAGE_JUMP),)
    return Decision(events=events, new_stage=str(new_stage))


def decide_drop(ticket: Ticket, actor: str) -> Decision:
    admission.require_direct_actor(actor, "drop_ticket")
    if ticket.stage == "done":
        raise PlannerError(
            ErrorCode.validation, "done tickets cannot be dropped", {"stage": "done"}
        )
    if ticket.stage == "dropped":
        raise PlannerError(ErrorCode.validation, "ticket is already dropped", {"stage": "dropped"})
    events = (_stage_change(ticket.stage, "dropped", CAUSE_DROP),)
    return Decision(events=events, new_stage="dropped")


def decide_scope_change(
    ticket: Ticket,
    ceiling: str,
    at_cap: AtCap,
    actor: str,
    *,
    worker_type_definition: WorkerTypeDefinition,
) -> Decision:
    admission.require_direct_actor(actor, "change_scope")
    if ceiling not in worker_type_definition.ceiling_range():
        raise PlannerError(
            ErrorCode.scope_invalid,
            "ceiling must be a linear stage",
            {"ceiling": ceiling},
        )
    events = (
        EventSpec(
            EventKind.scope_changed,
            {"ceiling": str(ceiling), "at_cap": at_cap.value, "cause": CAUSE_DIRECT_SCOPE},
        ),
    )
    return Decision(events=events, new_ceiling=str(ceiling), new_at_cap=at_cap)
