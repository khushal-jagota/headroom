"""Ticket domain shapes: the state machine order, the four fields, the scope pair,
and the ticket row."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, NotRequired, TypedDict

from planner.core.contracts import Priority

# §3.3 ticket title length cap. The DDL carries the matching literal
# `CHECK (length(title) <= 200)` as the DB-level backstop; this constant is the
# single enforcement source the write paths pass to admission.validate_title.
TITLE_MAX_CHARS: Final = 200


class TicketState(StrEnum):        # §4.1, exact order
    needs_success = "needs_success"
    needs_approach = "needs_approach"
    needs_plan = "needs_plan"
    in_progress = "in_progress"
    needs_review = "needs_review"
    done = "done"
    dropped = "dropped"            # terminal, direct-only, outside the linear order


# Linear pipeline order (dropped excluded). Index comparison implements "<= ceiling".
# ceiling and next_ceiling values are restricted to members of this tuple (never dropped).
STATE_ORDER: Final[tuple[TicketState, ...]] = (
    TicketState.needs_success, TicketState.needs_approach, TicketState.needs_plan,
    TicketState.in_progress, TicketState.needs_review, TicketState.done,
)


class FieldName(StrEnum):          # §4.2 — the exactly-four field keys
    success = "success"
    approach = "approach"
    plan = "plan"
    result = "result"


class AtCap(StrEnum):              # §4.3
    stop = "stop"
    propose = "propose"


class TicketStatus(StrEnum):       # durable state-of-control, written by data-layer transitions
    empty = "empty"
    agent_running_step = "agent_running_step"
    awaiting_approval = "awaiting_approval"
    user_takeover = "user_takeover"
    errored = "errored"


# §4.2 table — gating field per pre-terminal state. needs_review has no proposal gate.
GATING_FIELD: Final[dict[TicketState, FieldName]] = {
    TicketState.needs_success: FieldName.success,
    TicketState.needs_approach: FieldName.approach,
    TicketState.needs_plan: FieldName.plan,
    TicketState.in_progress: FieldName.result,
}

# §4.2 table — accepted proposal advances to. in_progress -> needs_review is the
# default route; the ceiling=done special case (§4.4.5) is resolution-engine logic,
# not a second table entry.
ADVANCE_TARGET: Final[dict[TicketState, TicketState]] = {
    TicketState.needs_success: TicketState.needs_approach,
    TicketState.needs_approach: TicketState.needs_plan,
    TicketState.needs_plan: TicketState.in_progress,
    TicketState.in_progress: TicketState.needs_review,
}


@dataclass(frozen=True)
class Proposal:                    # §4.2 proposal slot
    body: str
    proposed_by: str               # actor string: "agent", run id context, or PLAN_ACTOR
    created_at: int


@dataclass
class FieldSlot:                   # §4.2 — one of the four field objects
    value: str | None = None       # canonical; resolution engine is the only writer
    proposal: Proposal | None = None
    user_note: str | None = None   # preserved user guidance for this field / step


@dataclass
class TicketFields:                # tickets.fields JSON column, exactly four keys
    success: FieldSlot = field(default_factory=FieldSlot)
    approach: FieldSlot = field(default_factory=FieldSlot)
    plan: FieldSlot = field(default_factory=FieldSlot)
    result: FieldSlot = field(default_factory=FieldSlot)


# --- the scope pair (§4.4.7) ---
NO_FURTHER: Final = "none"                     # wire sentinel: ceiling = the newly entered state
NextCeiling = TicketState | Literal["none"]    # valid TicketState values are STATE_ORDER members


@dataclass(frozen=True)
class ScopePair:                   # required on every direct accept/edit-accept
    next_ceiling: NextCeiling
    at_cap: AtCap


# --- request bodies (§9 wire shapes) ---
# Most legacy bodies below are partial wire shapes: an absent key takes its documented
# default and unknown keys are ignored. External-work bodies are intentionally strict:
# required keys are encoded here and their API marshal rejects unknown keys.


class CreateTicketBody(TypedDict, total=False):   # POST /tickets
    title: str                     # default ""
    user_note: str                 # default ""; preserved intake context / user guidance
    priority: str | None           # Priority value; default P3
    deadline: str | None           # ISO date
    project: str | None            # legacy project name
    project_id: str | None
    sprint_id: str | None
    sprint_item_id: str | None


class ReconcileTicketFromExternalWorkBody(TypedDict):
    state: str
    user_note: str
    recap: NotRequired[str]
    success: NotRequired[str]
    approach: NotRequired[str]
    plan: NotRequired[str]
    result: NotRequired[str]


class CreateTicketFromExternalWorkBody(ReconcileTicketFromExternalWorkBody):
    title: str
    priority: NotRequired[str | None]
    deadline: NotRequired[str | None]
    project: NotRequired[str | None]
    project_id: NotRequired[str | None]
    sprint_id: NotRequired[str | None]
    sprint_item_id: NotRequired[str | None]


class ProposeBody(TypedDict, total=False):        # POST /tickets/{id}/propose/{field}
    body: str                      # default ""


class ProposeWithRecapBody(TypedDict, total=False):  # POST /tickets/{id}/propose
    body: str                      # default ""
    recap: str                     # required non-empty by the writer


class AcceptBody(TypedDict, total=False):         # POST /tickets/{id}/accept/{field}
    edited_body: str | None        # direct edit applied before resolution
    next_ceiling: str | None       # TicketState value or NO_FURTHER; scope pair (§4.4.7)
    at_cap: str | None             # AtCap value; scope pair (§4.4.7)


class NoteBody(TypedDict, total=False):           # PUT /tickets/{id}/notes/{field}
    note: str | None               # legacy key; null clears the user note
    user_note: str | None          # preferred key; null clears the user note


class RecapBody(TypedDict, total=False):          # PUT /tickets/{id}/recap
    body: str                      # default ""


class ValueEditBody(TypedDict, total=False):      # PUT /tickets/{id}/value/{field}
    body: str                      # default ""


class RevisionMessageBody(TypedDict, total=False):  # POST /tickets/{id}/return-for-revision
    message: str                   # required non-empty by the writer


class ScopeBody(TypedDict, total=False):          # POST /tickets/{id}/scope
    ceiling: str | None            # TicketState value; route requires it (scope_missing)
    at_cap: str | None             # AtCap value; route requires it (scope_missing)


class StateBody(TypedDict, total=False):          # POST /tickets/{id}/state
    to: str                        # TicketState value; required (default "" is rejected)


class LinkBody(TypedDict, total=False):           # POST /links (ticket-anchored, homed here)
    from_id: str                   # required (default "" fails endpoint checks)
    to_id: str                     # required (default "" fails endpoint checks)
    kind: str                      # LinkKind value; required (default "" is rejected)


@dataclass
class Ticket:                      # §3.3 — column names match exactly
    id: str
    title: str                     # <= TITLE_MAX_CHARS (200), every write path
    state: TicketState
    priority: Priority             # default P3
    deadline: str | None           # ISO date
    project_id: str | None         # NULL when parented (derived)
    project_name: str | None
    sprint_item_id: str | None
    sprint_id: str | None          # writable only when sprint_item_id IS NULL
    recap: str                     # writable only past needs_success
    user_note: str                 # preserved intake context / user guidance
    ceiling: TicketState           # default needs_success (R2); restricted to STATE_ORDER
    at_cap: AtCap                  # default propose (R2)
    ticket_status: TicketStatus    # durable state-of-control; transition functions write it
    chat_session_key: str | None   # the ticket-mind's durable Hermes session_key
    alias: str | None              # migration "Ticket ID:" (§12), unique when present
    fields: TicketFields
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class TicketDeletion:
    """The deleted identity plus surviving resources affected by the transaction."""

    ticket_id: str
    title: str
    day_ids: tuple[str, ...]
    sprint_item_ids: tuple[str, ...]
    sprint_ids: tuple[str, ...]
    linked_entity_ids: tuple[str, ...]
