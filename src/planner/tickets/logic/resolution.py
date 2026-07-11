"""§4.4 resolution semantics as pure Decisions. _accept_gating_proposal is the
sole constructor of any Decision that traverses the gating-acceptance edges 1-5
(auto and direct alike); _state_change is the sole constructor of state_changed
EventSpecs. Together with data._apply_decision (the sole appender) this is the
"one canonical writer per transition" guarantee (§4.4.6)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from planner.core.contracts import ErrorCode, EventKind, PlannerError
from planner.tickets.contracts import (
    AtCap,
    FieldName,
    FieldSlot,
    NextCeiling,
    Proposal,
    ScopePair,
    Ticket,
    TicketState,
    TicketStatus,
)
from planner.tickets.logic import admission, fields_codec, machine
from planner.tickets.logic.decisions import Decision, EventSpec

if TYPE_CHECKING:
    from planner.tickets.logic.coding_bridge import WorkflowDefinition

CAUSE_AUTO_ACCEPT: Final[str] = "auto_accept"
CAUSE_DIRECT_ACCEPT: Final[str] = "direct_accept"
CAUSE_DIRECT_STATE_JUMP: Final[str] = "direct_state_jump"
CAUSE_DROP: Final[str] = "drop"
CAUSE_ONWARD_SCOPE: Final[str] = "onward_scope"
CAUSE_DIRECT_SCOPE: Final[str] = "direct_scope"
RESOLVED_BY_AUTO: Final[str] = "auto"
RESOLVED_BY_DIRECT: Final[str] = "direct"


def _state_change(old: str, new: str, cause: str) -> EventSpec:
    return EventSpec(EventKind.state_changed, {"from": str(old), "to": str(new), "cause": cause})


def _accept_gating_proposal(
    ticket: Ticket,
    field: FieldName | str,
    stored_body: str,
    resolved_by: str,
    edited: bool,
    scope: ScopePair | None,
    cause: str,
    superseded_body: str | None = None,
    *,
    definition: WorkflowDefinition | None = None,
) -> Decision:
    slot = fields_codec.get_slot(ticket.fields, str(field))
    new_slot = FieldSlot(value=stored_body, proposal=None, user_note=slot.user_note)
    new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
    new_state = machine.advance_target(ticket.state, definition=definition)
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
    events.append(_state_change(ticket.state, new_state, cause))
    new_ceiling: str | None = None
    new_at_cap: AtCap | None = None
    if scope is not None:
        new_ceiling = scope.next_ceiling      # already a resolved ceiling id (str)
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
        new_state=new_state,
        new_ceiling=new_ceiling,
        new_at_cap=new_at_cap,
    )


def decide_file_proposal(
    ticket: Ticket,
    field: FieldName | str,
    body: str,
    actor: str,
    now: int,
    *,
    definition: WorkflowDefinition | None = None,
) -> Decision:
    admission.validate_body(body, "proposal body")
    admission.check_agent_proposal(
        ticket.state, ticket.ceiling, ticket.at_cap, field, definition=definition
    )
    slot = fields_codec.get_slot(ticket.fields, str(field))
    superseded_body = slot.proposal.body if slot.proposal is not None else None
    if (
        machine.auto_accept_target(ticket.state, ticket.ceiling, field, definition=definition)
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
            definition=definition,
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
    field: FieldName | str,
    actor: str,
    edited_body: str | None,
    next_ceiling: NextCeiling | None,
    at_cap: AtCap | None,
    *,
    definition: WorkflowDefinition | None = None,
) -> Decision:
    admission.require_direct_actor(actor, "accept_proposal")
    if edited_body is not None:
        admission.validate_body(edited_body, "edit-accept text")
    edited = edited_body is not None
    if ticket.state == TicketState.dropped:
        raise PlannerError(
            ErrorCode.validation, "dropped tickets cannot be accepted", {"state": "dropped"}
        )
    slot = fields_codec.get_slot(ticket.fields, str(field))
    if slot.proposal is None:
        raise PlannerError(
            ErrorCode.not_found,
            "no pending proposal on field",
            {"ticket_id": ticket.id, "field": str(field)},
        )
    stored_body = edited_body if edited_body is not None else slot.proposal.body
    if str(field) == str(machine.gating_field(ticket.state, definition=definition)):
        new_state = machine.advance_target(ticket.state, definition=definition)
        scope = machine.resolve_scope(new_state, next_ceiling, at_cap, definition=definition)
        return _accept_gating_proposal(
            ticket,
            field,
            stored_body=stored_body,
            resolved_by=RESOLVED_BY_DIRECT,
            edited=edited,
            scope=scope,
            cause=CAUSE_DIRECT_ACCEPT,
            definition=definition,
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
    field: FieldName | str,
    new_body: str,
    actor: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> Decision:
    """§4.2 direct edit of an already-*passed* settled value. The value stays written
    solely by the resolution engine; this is a tightly-guarded direct write path that
    never touches state/ceiling. It rejects dropped tickets, an unset value, a field
    carrying a live proposal, and the current gating or any future field."""
    admission.require_direct_actor(actor, "edit_field_value")
    admission.validate_body(new_body, "field value")
    if ticket.state == TicketState.dropped:
        raise PlannerError(
            ErrorCode.validation, "dropped tickets cannot be edited", {"state": "dropped"}
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
    if not machine.field_is_passed(field, ticket.state, definition=definition):
        raise PlannerError(
            ErrorCode.validation,
            "field is not yet passed",
            {"field": str(field), "state": str(ticket.state)},
        )
    new_slot = FieldSlot(value=new_body, proposal=None, user_note=slot.user_note)
    new_fields = fields_codec.with_slot(ticket.fields, str(field), new_slot)
    return Decision(
        events=(
            EventSpec(EventKind.field_value_edited, {"field": str(field), "body": new_body}),
        ),
        new_fields=new_fields,
    )


def decide_return_for_revision(
    ticket: Ticket, actor: str, *, definition: WorkflowDefinition | None = None
) -> Decision:
    admission.require_direct_actor(actor, "return_for_revision")
    if ticket.ticket_status is TicketStatus.agent_running_step:
        raise PlannerError(
            ErrorCode.already_running,
            "the ticket worker is already revising this proposal",
            {"ticket_id": ticket.id},
        )
    if ticket.state in (TicketState.done, TicketState.dropped):
        raise PlannerError(
            ErrorCode.validation,
            "terminal tickets cannot be returned for revision",
            {"state": str(ticket.state)},
        )
    if ticket.chat_session_key is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket has no existing worker session",
            {"ticket_id": ticket.id},
        )
    field = machine.gating_field(ticket.state, definition=definition)
    if field is None:
        raise PlannerError(
            ErrorCode.validation,
            "ticket has no approval item to return",
            {"state": str(ticket.state)},
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


def decide_state_jump(ticket: Ticket, new_state: TicketState, actor: str) -> Decision:
    admission.require_direct_actor(actor, "set_state")
    if new_state == TicketState.dropped:
        raise PlannerError(ErrorCode.validation, "use the drop action")
    if ticket.state == TicketState.dropped:
        raise PlannerError(ErrorCode.validation, "dropped is terminal")
    if ticket.state == TicketState.needs_kickoff or new_state == TicketState.needs_kickoff:
        raise PlannerError(
            ErrorCode.validation,
            "kickoff state changes only through kickoff approval",
        )
    if new_state == ticket.state:
        raise PlannerError(ErrorCode.validation, "ticket already in that state")
    events = (_state_change(ticket.state, new_state, CAUSE_DIRECT_STATE_JUMP),)
    return Decision(events=events, new_state=new_state)


def decide_drop(ticket: Ticket, actor: str) -> Decision:
    admission.require_direct_actor(actor, "drop_ticket")
    if ticket.state == TicketState.done:
        raise PlannerError(
            ErrorCode.validation, "done tickets cannot be dropped", {"state": "done"}
        )
    if ticket.state == TicketState.dropped:
        raise PlannerError(
            ErrorCode.validation, "ticket is already dropped", {"state": "dropped"}
        )
    events = (_state_change(ticket.state, TicketState.dropped, CAUSE_DROP),)
    return Decision(events=events, new_state=TicketState.dropped)


def decide_scope_change(
    ticket: Ticket,
    ceiling: str,
    at_cap: AtCap,
    actor: str,
    *,
    definition: WorkflowDefinition | None = None,
) -> Decision:
    admission.require_direct_actor(actor, "change_scope")
    machine.validate_ceiling(ceiling, definition=definition)
    events = (
        EventSpec(
            EventKind.scope_changed,
            {"ceiling": str(ceiling), "at_cap": at_cap.value, "cause": CAUSE_DIRECT_SCOPE},
        ),
    )
    return Decision(events=events, new_ceiling=str(ceiling), new_at_cap=at_cap)
