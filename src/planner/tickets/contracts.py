"""Ticket domain shapes: the state machine order, the ticket fields, the scope pair,
and the ticket row."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal, NotRequired, TypedDict

from planner.core.contracts import Priority

# §3.3 ticket title length cap. The DDL carries the matching literal
# `CHECK (length(title) <= 200)` as the DB-level backstop; this constant is the
# single enforcement source the write paths pass to admission.validate_title.
TITLE_MAX_CHARS: Final = 200


class TicketState(StrEnum):        # linear lifecycle, exact order
    needs_kickoff = "needs_kickoff"
    needs_success = "needs_success"
    needs_approach = "needs_approach"
    needs_plan = "needs_plan"
    needs_implementation = "needs_implementation"
    needs_closeout = "needs_closeout"
    done = "done"
    dropped = "dropped"            # terminal, direct-only, outside the linear order


# Linear pipeline order (dropped excluded). Index comparison implements "<= ceiling".
# ceiling and next_ceiling values are restricted to members of this tuple (never dropped).
STATE_ORDER: Final[tuple[TicketState, ...]] = (
    TicketState.needs_kickoff, TicketState.needs_success, TicketState.needs_approach,
    TicketState.needs_plan, TicketState.needs_implementation, TicketState.needs_closeout,
    TicketState.done,
)

WORKER_STATE_ORDER: Final[tuple[TicketState, ...]] = (
    TicketState.needs_success, TicketState.needs_approach, TicketState.needs_plan,
    TicketState.needs_implementation, TicketState.needs_closeout, TicketState.done,
)


class FieldName(StrEnum):          # ordinary gated field keys
    kickoff = "kickoff"
    success = "success"
    approach = "approach"
    plan = "plan"
    implementation = "implementation"
    closeout = "closeout"


class AtCap(StrEnum):              # §4.3
    stop = "stop"
    propose = "propose"


class Implementer(StrEnum):
    khushal = "khushal"
    panels_worker = "panels_worker"
    hermes_codex = "hermes_codex"
    hermes_claude = "hermes_claude"


class TicketStatus(StrEnum):       # durable state-of-control, written by data-layer transitions
    empty = "empty"
    agent_running_step = "agent_running_step"
    awaiting_approval = "awaiting_approval"
    user_takeover = "user_takeover"
    errored = "errored"


# Gating field per non-terminal linear state: each state gates its same-named field.
GATING_FIELD: Final[dict[TicketState, FieldName]] = {
    TicketState.needs_kickoff: FieldName.kickoff,
    TicketState.needs_success: FieldName.success,
    TicketState.needs_approach: FieldName.approach,
    TicketState.needs_plan: FieldName.plan,
    TicketState.needs_implementation: FieldName.implementation,
    TicketState.needs_closeout: FieldName.closeout,
}

# Accepted proposal advances one linear step. needs_closeout advances to done through
# the same ordinary machinery; there is no state that skips a gate.
ADVANCE_TARGET: Final[dict[TicketState, TicketState]] = {
    TicketState.needs_kickoff: TicketState.needs_success,
    TicketState.needs_success: TicketState.needs_approach,
    TicketState.needs_approach: TicketState.needs_plan,
    TicketState.needs_plan: TicketState.needs_implementation,
    TicketState.needs_implementation: TicketState.needs_closeout,
    TicketState.needs_closeout: TicketState.done,
}


@dataclass(frozen=True)
class Proposal:                    # §4.2 proposal slot
    body: str
    proposed_by: str               # actor string: "agent", run id context, or PLAN_ACTOR
    created_at: int


@dataclass
class FieldSlot:                   # one ordinary field object
    value: str | None = None       # canonical; resolution engine is the only writer
    proposal: Proposal | None = None
    user_note: str | None = None   # preserved user guidance for this field / step


@dataclass(frozen=True)
class TicketFields:                # tickets.fields JSON column, generic over the type's fields
    """An ordered, READ-ONLY map field_id -> FieldSlot. The key order is the definition's
    declared field order; the codec relies on it for a stable, byte-identical JSON key
    order.

    ``slots`` is exposed as a ``MappingProxyType`` so the only way to change a slot is
    through ``fields_codec.with_slot`` (copy-on-write) → the resolution engine — the same
    value-object boundary the old fixed struct enforced. The constructor accepts any
    ``Mapping`` and wraps a private copy, so a caller cannot retain a mutable handle to
    the backing dict. (FieldSlot's own field-level mutability is pre-existing and left
    as-is; the boundary this enforces is against reassigning or inserting a slot.)"""

    slots: Mapping[str, FieldSlot] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Wrap a fresh private copy in a read-only proxy: reassigning or inserting a slot
        # on `.slots` raises, and the caller's dict cannot alias the stored mapping.
        object.__setattr__(self, "slots", MappingProxyType(dict(self.slots)))

    @classmethod
    def empty(cls, field_ids: tuple[str, ...]) -> TicketFields:
        """A fresh set of empty slots, one per declared field id, in declared order."""
        return cls({fid: FieldSlot() for fid in field_ids})


# --- the scope pair (§4.4.7) ---
NO_FURTHER: Final = "none"                     # wire sentinel: ceiling = the newly entered state
# A ceiling id is any member of the type's ceiling_range (a str); "none" is the wire
# sentinel meaning "the newly entered state". For coding these ids are TicketState values.
NextCeiling = str | Literal["none"]            # was: TicketState | Literal["none"]


@dataclass(frozen=True)
class ScopePair:                   # required on every direct accept/edit-accept
    next_ceiling: str              # a resolved ceiling id (resolve_scope concretizes "none")
    at_cap: AtCap


# --- request bodies (§9 wire shapes) ---
# Most legacy bodies below are partial wire shapes: an absent key takes its documented
# default and unknown keys are ignored. External-work bodies are intentionally strict:
# required keys are encoded here and their API marshal rejects unknown keys.


class CreateTicketBody(TypedDict, total=False):   # POST /tickets
    ticket_type: str               # required registry type id (no ingress default)
    title: str                     # default ""
    kickoff_note: str              # default ""; proposed intake context / user guidance
    priority: str | None           # Priority value; default P3
    deadline: str | None           # ISO date
    project: str | None            # legacy project name
    project_id: str | None
    sprint_id: str | None
    sprint_item_id: str | None


class TicketEdit(TypedDict, total=False):         # PATCH /tickets/{id}, parsed values
    title: str
    priority: Priority
    deadline: str | None
    implementer: Implementer | None
    project_id: str | None
    sprint_id: str | None


class ReconcileTicketFromExternalWorkBody(TypedDict):
    state: str
    kickoff_note: str
    recap: NotRequired[str]
    success: NotRequired[str]
    approach: NotRequired[str]
    plan: NotRequired[str]
    implementation: NotRequired[str]
    closeout: NotRequired[str]


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
    state: str                     # linear stage id; a TicketState for coding, a bare str otherwise
    priority: Priority             # default P3
    deadline: str | None           # ISO date
    project_id: str | None         # NULL when parented (derived)
    project_name: str | None
    sprint_item_id: str | None
    sprint_id: str | None          # writable only when sprint_item_id IS NULL
    recap: str                     # writable only past needs_success
    ceiling: str                   # ceiling id; a member of the type's ceiling_range
    at_cap: AtCap                  # default propose (R2)
    ticket_status: TicketStatus    # durable state-of-control; transition functions write it
    implementer: Implementer | None  # human-overridable execution route
    chat_session_key: str | None   # the ticket-mind's durable Hermes session_key
    alias: str | None              # migration "Ticket ID:" (§12), unique when present
    fields: TicketFields
    created_at: int
    updated_at: int
    ticket_type: str = "coding"    # registry type id resolved on load; trailing default


@dataclass(frozen=True)
class TicketDeletion:
    """The deleted identity plus surviving resources affected by the transaction."""

    ticket_id: str
    title: str
    day_ids: tuple[str, ...]
    sprint_item_ids: tuple[str, ...]
    sprint_ids: tuple[str, ...]
    linked_entity_ids: tuple[str, ...]
