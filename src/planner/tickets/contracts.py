"""Ticket-owned domain shapes: fields, scope, requests, and stored Ticket rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final, Literal, NotRequired, Required, TypedDict

from planner.core.contracts import Priority

# §3.3 ticket title length cap. The DDL carries the matching literal
# `CHECK (length(title) <= 200)` as the DB-level backstop; this constant is the
# single enforcement source the write paths pass to admission.validate_title.
TITLE_MAX_CHARS: Final = 200


class AtCap(StrEnum):  # §4.3
    stop = "stop"
    propose = "propose"


class StageOwnershipMode(StrEnum):
    worker = "worker"
    user = "user"
    paired = "paired"


class TicketStatus(StrEnum):  # durable state-of-control, written by data-layer transitions
    empty = "empty"
    blocked = "blocked"  # empty's stand-in while a live blocker exists
    agent = "agent"
    paired = "paired"
    awaiting_approval = "awaiting_approval"
    needs_user = "needs_user"
    user = "user"
    errored = "errored"


class WorkspaceActivityState(StrEnum):
    """ACP activity facts that may affect a Ticket's Workspace dot."""

    connecting = "connecting"
    loading = "loading"
    idle = "idle"
    thinking = "thinking"
    working = "working"
    compacting = "compacting"
    waiting_for_permission = "waiting_for_permission"
    interrupted = "interrupted"
    failed = "failed"


class WorkspaceAgentReplyState(StrEnum):
    """Whether a Worker reply (or permission ask) is waiting, and whether it was seen."""

    none = "none"
    unseen = "unseen"
    seen = "seen"


@dataclass(frozen=True)
class WorkspaceAgentReplyFacts:
    """The conversation-projection flags a Ticket's reply dot is derived from.

    Whether a worker is running now, and whether it is waiting on a permission ask, are
    the conversation system's facts and are read in the board route, not here.
    """

    has_completed_response_awaiting_user: bool = False
    has_completed_response: bool = False
    has_pending_permission: bool = False


@dataclass(frozen=True)
class Proposal:  # §4.2 proposal slot
    body: str
    proposed_by: str  # actor string: "agent", run id context, or PLAN_ACTOR
    created_at: int


@dataclass
class FieldSlot:  # one ordinary field object
    value: str | None = None  # canonical; proposal resolver is the only writer
    proposal: Proposal | None = None
    user_note: str | None = None  # preserved user guidance for this field / step


@dataclass(frozen=True)
class TicketFields:  # tickets.fields JSON column, generic over the type's fields
    """An ordered, READ-ONLY map field_id -> FieldSlot. The key order is the definition's
    declared field order; the codec relies on it for a stable, byte-identical JSON key
    order.

    ``slots`` is exposed as a ``MappingProxyType`` so the only way to change a slot is
    through ``fields_codec.with_slot`` (copy-on-write) → the proposal resolver — the same
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
NO_FURTHER: Final = "none"  # wire sentinel: ceiling = the newly entered Stage
# A ceiling id is any member of the type's ceiling_range (a str); "none" is the wire
# sentinel meaning "the newly entered Stage".
NextCeiling = str | Literal["none"]


@dataclass(frozen=True)
class ScopePair:  # required on every direct accept/edit-accept
    next_ceiling: str  # a resolved ceiling id (resolve_scope concretizes "none")
    at_cap: AtCap


# --- request bodies (§9 wire shapes) ---
# Most legacy bodies below are partial wire shapes: an absent key takes its documented
# default and unknown keys are ignored. External-work bodies are intentionally strict:
# required keys are encoded here and their API marshal rejects unknown keys.


class CreateTicketBody(TypedDict, total=False):  # POST /tickets
    worker_type: Required[str]  # required registry type id (no ingress default)
    employee_backend: str
    title: str  # default ""
    kickoff_note: str  # default ""; proposed intake context / user guidance
    priority: str | None  # Priority value; default P3
    deadline: str | None  # ISO date
    project: str | None  # legacy project name
    project_id: str | None
    sprint_id: str | None
    sprint_item_id: str | None
    blocked_by_ticket_ids: list[str]


class TicketEdit(TypedDict, total=False):  # PATCH /tickets/{id}, parsed values
    title: str
    priority: Priority
    deadline: str | None
    project_id: str | None
    sprint_id: str | None


class ReconcileTicketFromExternalWorkBody(TypedDict):
    stage: str
    kickoff_note: str
    recap: NotRequired[str]


class CreateTicketFromExternalWorkBody(ReconcileTicketFromExternalWorkBody):
    title: str
    worker_type: str
    employee_backend: NotRequired[str]
    priority: NotRequired[str | None]
    deadline: NotRequired[str | None]
    project: NotRequired[str | None]
    project_id: NotRequired[str | None]
    sprint_id: NotRequired[str | None]
    sprint_item_id: NotRequired[str | None]
    blocked_by_ticket_ids: NotRequired[list[str]]


class ProposeBody(TypedDict, total=False):  # POST /tickets/{id}/propose/{field}
    body: str  # default ""


class ProposeWithRecapBody(TypedDict, total=False):  # POST /tickets/{id}/propose
    body: str  # default ""
    recap: str  # required non-empty by the writer


class AcceptBody(TypedDict, total=False):  # POST /tickets/{id}/accept/{field}
    edited_body: str | None  # direct edit applied before resolution
    next_ceiling: str | None  # Stage id or NO_FURTHER; scope pair (§4.4.7)
    at_cap: str | None  # AtCap value; scope pair (§4.4.7)


class NoteBody(TypedDict, total=False):  # PUT /tickets/{id}/notes/{field}
    note: str | None  # legacy key; null clears the user note
    user_note: str | None  # preferred key; null clears the user note


class RecapBody(TypedDict, total=False):  # PUT /tickets/{id}/recap
    body: str  # default ""


class ValueEditBody(TypedDict, total=False):  # PUT /tickets/{id}/value/{field}
    body: str  # default ""


class RevisionMessageBody(TypedDict, total=False):  # POST /tickets/{id}/return-for-revision
    message: str  # required non-empty by the writer


class ScopeBody(TypedDict, total=False):  # POST /tickets/{id}/scope
    ceiling: str | None  # Stage id; route requires it (scope_missing)
    at_cap: str | None  # AtCap value; route requires it (scope_missing)


class StageBody(TypedDict, total=False):  # POST /tickets/{id}/stage
    to_stage: str  # Stage id; required (default "" is rejected)


class EmployeeConfigurationBody(TypedDict):
    employee_backend: str
    employee_launch_model: str | None
    employee_launch_reasoning_effort: str | None


class LinkBody(TypedDict, total=False):  # POST /links (ticket-anchored, homed here)
    from_id: str  # required (default "" fails endpoint checks)
    to_id: str  # required (default "" fails endpoint checks)
    kind: str  # LinkKind value; required (default "" is rejected)


@dataclass
class Ticket:  # §3.3 — column names match exactly
    id: str
    title: str  # <= TITLE_MAX_CHARS (200), every write path
    worker_type: str  # immutable registry id selected at creation
    employee_backend: str  # last-chosen backend for this Ticket's worker
    # Last-chosen model and reasoning effort; null means the backend's own default.
    employee_launch_model: str | None = field(default=None, kw_only=True)
    employee_launch_reasoning_effort: str | None = field(default=None, kw_only=True)
    stage: str  # directly stored Stage id
    priority: Priority  # default P3
    deadline: str | None  # ISO date
    project_id: str | None  # NULL when parented (derived)
    project_name: str | None
    sprint_item_id: str | None
    sprint_id: str | None  # writable only when sprint_item_id IS NULL
    recap: str  # writable only past the type's first worker Stage
    ceiling: str  # ceiling id; a member of the type's ceiling_range
    at_cap: AtCap  # default propose (R2)
    ticket_status: TicketStatus  # durable state-of-control; transition functions write it
    # When ticket_status last actually changed. Claiming a Ticket for a worker step
    # captures it, and giving that claim back compares it, so a late release cannot erase
    # a later transition that happens to have landed on the same status value.
    ticket_status_changed_at: int
    backend_error: str | None  # concrete confirmed backend Worker failure, else NULL
    stage_ownership_overrides: Mapping[str, StageOwnershipMode]
    default_stage_ownership_mode: StageOwnershipMode | None
    effective_stage_ownership_mode: StageOwnershipMode | None
    employee_session_id: str | None  # the Ticket's conversation link (column name is frozen)
    alias: str | None  # migration "Ticket ID:" (§12), unique when present
    fields: TicketFields
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class EmployeeLaunchConfiguration:
    """What the Ticket's worker last ran on: backend, model, reasoning effort.

    Kept up to date as the Ticket's conversation changes, so a fresh conversation
    starts from where the last one ended. The field names are the storage and wire
    names of the three columns and are frozen with them.
    """

    employee_backend: str
    employee_launch_model: str | None
    employee_launch_reasoning_effort: str | None


@dataclass(frozen=True)
class EmployeeSessionIdTransition:
    expected_employee_session_id: str | None
    candidate_employee_session_id: str


@dataclass(frozen=True)
class EmployeeSessionHistoryMessage:
    role: str
    text: str
    created_at: int


@dataclass(frozen=True)
class EmployeeSessionHistory:
    messages: tuple[EmployeeSessionHistoryMessage, ...]
    employee_session_id: str | None


@dataclass(frozen=True)
class TicketDeletion:
    """The deleted identity plus surviving resources affected by the transaction."""

    ticket_id: str
    title: str
    day_ids: tuple[str, ...]
    sprint_item_ids: tuple[str, ...]
    sprint_ids: tuple[str, ...]
    linked_entity_ids: tuple[str, ...]
