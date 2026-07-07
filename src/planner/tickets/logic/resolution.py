"""§4.4 resolution semantics as pure Decisions. _accept_gating_proposal is the
sole constructor of any Decision that traverses the gating-acceptance edges 1-5
(auto and human alike); _state_change is the sole constructor of state_changed
EventSpecs. Together with data._apply_decision (the sole appender) this is the
"one canonical writer per transition" guarantee (§4.4.6)."""

from __future__ import annotations

from typing import Final

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
)
from planner.tickets.logic import admission, fields_codec, machine
from planner.tickets.logic.decisions import Decision, EventSpec

CAUSE_AUTO_ACCEPT: Final[str] = "auto_accept"
CAUSE_HUMAN_ACCEPT: Final[str] = "human_accept"
CAUSE_REVIEW_APPROVE: Final[str] = "review_approve"
CAUSE_HUMAN_STATE_JUMP: Final[str] = "human_state_jump"
CAUSE_DROP: Final[str] = "drop"
CAUSE_ONWARD_SCOPE: Final[str] = "onward_scope"
CAUSE_HUMAN_SCOPE: Final[str] = "human_scope"
RESOLVED_BY_AUTO: Final[str] = "auto"
RESOLVED_BY_HUMAN: Final[str] = "human"


def _state_change(old: TicketState, new: TicketState, cause: str) -> EventSpec:
    return EventSpec(EventKind.state_changed, {"from": old.value, "to": new.value, "cause": cause})


def _accept_gating_proposal(
    ticket: Ticket,
    field: FieldName,
    stored_body: str,
    resolved_by: str,
    edited: bool,
    scope: ScopePair | None,
    cause: str,
    superseded_body: str | None = None,
) -> Decision:
    slot = fields_codec.get_slot(ticket.fields, field)
    new_slot = FieldSlot(value=stored_body, proposal=None, notes=slot.notes)
    new_fields = fields_codec.with_slot(ticket.fields, field, new_slot)
    new_state = machine.advance_target(ticket.state, ticket.ceiling)
    events: list[EventSpec] = []
    if superseded_body is not None:
        events.append(
            EventSpec(
                EventKind.proposal_superseded,
                {"field": field.value, "replaced_body": superseded_body},
            )
        )
    events.append(
        EventSpec(
            EventKind.proposal_accepted,
            {
                "field": field.value,
                "body": stored_body,
                "resolved_by": resolved_by,
                "edited": edited,
            },
        )
    )
    events.append(_state_change(ticket.state, new_state, cause))
    new_ceiling: TicketState | None = None
    new_at_cap: AtCap | None = None
    if scope is not None:
        ceiling = scope.next_ceiling
        assert isinstance(ceiling, TicketState)  # resolve_scope always yields a concrete state
        new_ceiling = ceiling
        new_at_cap = scope.at_cap
        events.append(
            EventSpec(
                EventKind.scope_changed,
                {
                    "ceiling": ceiling.value,
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
    ticket: Ticket, field: FieldName, body: str, actor: str, now: int
) -> Decision:
    admission.validate_body(body, "proposal body")
    admission.check_agent_proposal(ticket.state, ticket.ceiling, ticket.at_cap, field)
    slot = fields_codec.get_slot(ticket.fields, field)
    superseded_body = slot.proposal.body if slot.proposal is not None else None
    if machine.auto_accept_target(ticket.state, ticket.ceiling, field) is not None:
        return _accept_gating_proposal(
            ticket,
            field,
            stored_body=body,
            resolved_by=RESOLVED_BY_AUTO,
            edited=False,
            scope=None,
            cause=CAUSE_AUTO_ACCEPT,
            superseded_body=superseded_body,
        )
    new_slot = FieldSlot(
        value=slot.value,
        proposal=Proposal(body=body, proposed_by=actor, created_at=now),
        notes=slot.notes,
    )
    new_fields = fields_codec.with_slot(ticket.fields, field, new_slot)
    events: list[EventSpec] = []
    if superseded_body is not None:
        events.append(
            EventSpec(
                EventKind.proposal_superseded,
                {"field": field.value, "replaced_body": superseded_body},
            )
        )
    events.append(
        EventSpec(
            EventKind.proposal_filed,
            {"field": field.value, "body": body, "proposed_by": actor},
        )
    )
    return Decision(events=tuple(events), new_fields=new_fields)


def decide_accept(
    ticket: Ticket,
    field: FieldName,
    actor: str,
    edited_body: str | None,
    next_ceiling: NextCeiling | None,
    at_cap: AtCap | None,
) -> Decision:
    admission.require_human(actor, "accept_proposal")
    if edited_body is not None:
        admission.validate_body(edited_body, "edit-accept text")
    edited = edited_body is not None
    if ticket.state is TicketState.dropped:
        raise PlannerError(
            ErrorCode.validation, "dropped tickets cannot be accepted", {"state": "dropped"}
        )
    slot = fields_codec.get_slot(ticket.fields, field)
    if slot.proposal is None:
        raise PlannerError(
            ErrorCode.not_found,
            "no pending proposal on field",
            {"ticket_id": ticket.id, "field": field.value},
        )
    stored_body = edited_body if edited_body is not None else slot.proposal.body
    if field is machine.gating_field(ticket.state):
        new_state = machine.advance_target(ticket.state, ticket.ceiling)
        scope = machine.resolve_scope(new_state, next_ceiling, at_cap)
        return _accept_gating_proposal(
            ticket,
            field,
            stored_body=stored_body,
            resolved_by=RESOLVED_BY_HUMAN,
            edited=edited,
            scope=scope,
            cause=CAUSE_HUMAN_ACCEPT,
        )
    new_slot = FieldSlot(value=stored_body, proposal=None, notes=slot.notes)
    new_fields = fields_codec.with_slot(ticket.fields, field, new_slot)
    events = (
        EventSpec(
            EventKind.proposal_accepted,
            {
                "field": field.value,
                "body": stored_body,
                "resolved_by": RESOLVED_BY_HUMAN,
                "edited": edited,
            },
        ),
    )
    return Decision(events=events, new_fields=new_fields)


def decide_edit_value(
    ticket: Ticket, field: FieldName, new_body: str, actor: str
) -> Decision:
    """§4.2 human edit of an already-*passed* settled value. The value stays written
    solely by the resolution engine; this is a tightly-guarded human write path that
    never touches state/ceiling. It rejects dropped tickets, an unset value, a field
    carrying a live proposal, and the current gating or any future field."""
    admission.require_human(actor, "edit_field_value")
    admission.validate_body(new_body, "field value")
    if ticket.state is TicketState.dropped:
        raise PlannerError(
            ErrorCode.validation, "dropped tickets cannot be edited", {"state": "dropped"}
        )
    slot = fields_codec.get_slot(ticket.fields, field)
    if slot.value is None:
        raise PlannerError(
            ErrorCode.validation, "field has no settled value to edit", {"field": field.value}
        )
    if slot.proposal is not None:
        raise PlannerError(
            ErrorCode.validation, "field has a pending proposal", {"field": field.value}
        )
    if not machine.field_is_passed(field, ticket.state):
        raise PlannerError(
            ErrorCode.validation,
            "field is not yet passed",
            {"field": field.value, "state": ticket.state.value},
        )
    new_slot = FieldSlot(value=new_body, proposal=None, notes=slot.notes)
    new_fields = fields_codec.with_slot(ticket.fields, field, new_slot)
    return Decision(
        events=(
            EventSpec(EventKind.field_value_edited, {"field": field.value, "body": new_body}),
        ),
        new_fields=new_fields,
    )


def decide_approve(ticket: Ticket, actor: str) -> Decision:
    admission.require_human(actor, "approve_review")
    if ticket.state is not TicketState.needs_review:
        raise PlannerError(
            ErrorCode.validation,
            "approve requires state needs_review",
            {"state": ticket.state.value},
        )
    events = (_state_change(ticket.state, TicketState.done, CAUSE_REVIEW_APPROVE),)
    return Decision(events=events, new_state=TicketState.done)


def decide_state_jump(ticket: Ticket, new_state: TicketState, actor: str) -> Decision:
    admission.require_human(actor, "set_state")
    if new_state is TicketState.dropped:
        raise PlannerError(ErrorCode.validation, "use the drop action")
    if ticket.state is TicketState.dropped:
        raise PlannerError(ErrorCode.validation, "dropped is terminal")
    if new_state is ticket.state:
        raise PlannerError(ErrorCode.validation, "ticket already in that state")
    events = (_state_change(ticket.state, new_state, CAUSE_HUMAN_STATE_JUMP),)
    return Decision(events=events, new_state=new_state)


def decide_drop(ticket: Ticket, actor: str) -> Decision:
    admission.require_human(actor, "drop_ticket")
    if ticket.state is TicketState.done:
        raise PlannerError(
            ErrorCode.validation, "done tickets cannot be dropped", {"state": "done"}
        )
    if ticket.state is TicketState.dropped:
        raise PlannerError(
            ErrorCode.validation, "ticket is already dropped", {"state": "dropped"}
        )
    events = (_state_change(ticket.state, TicketState.dropped, CAUSE_DROP),)
    return Decision(events=events, new_state=TicketState.dropped)


def decide_scope_change(
    ticket: Ticket, ceiling: TicketState, at_cap: AtCap, actor: str
) -> Decision:
    admission.require_human(actor, "change_scope")
    machine.validate_ceiling(ceiling)
    events = (
        EventSpec(
            EventKind.scope_changed,
            {"ceiling": ceiling.value, "at_cap": at_cap.value, "cause": CAUSE_HUMAN_SCOPE},
        ),
    )
    return Decision(events=events, new_ceiling=ceiling, new_at_cap=at_cap)
