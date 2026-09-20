"""Ticket-owned domain shapes: fields, scope, requests, and stored Ticket rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, NotRequired, Required, TypedDict

from planner.core.contracts import Principal, Priority

# §3.3 ticket title length cap. The DDL carries the matching literal
# `CHECK (length(title) <= 200)` as the DB-level backstop; this constant is the
# single enforcement source the write paths pass to admission.validate_title.
TITLE_MAX_CHARS: Final = 200


class StageOwnershipMode(StrEnum):
    worker = "worker"
    user = "user"


@dataclass(frozen=True, slots=True)
class SprintItemPriorityAnchor:
    id: str
    title: str
    priority: Priority


@dataclass(frozen=True, slots=True)
class ProjectPriorityAnchor:
    id: str
    name: str
    priority: Priority | None


@dataclass(frozen=True, slots=True)
class ResolvedTicketPriorityAnchors:
    """The authoritative placement context used when a Ticket is created."""

    sprint_item: SprintItemPriorityAnchor | None
    project: ProjectPriorityAnchor | None


class TicketStatus(StrEnum):  # durable state-of-control, written by data-layer transitions
    empty = "empty"
    blocked = "blocked"  # empty's stand-in while a live blocker exists
    agent = "agent"
    awaiting_approval = "awaiting_approval"
    errored = "errored"


class BoardCard(TypedDict):
    """The Ticket projection consumed by the Workspace rail."""

    id: str
    title: str
    priority: str
    deadline: str | None
    project_id: str | None
    project: str | None
    group_project_id: str | None
    group_project: str | None
    activity_at: int
    has_pending_proposal: bool
    ticket_status: str
    worker_type: str
    employee_backend: str
    stage: str
    stage_label: str
    gating_field: str | None
    gating_field_label: str | None
    is_done: bool
    is_dropped: bool
    blocked: bool
    conversation_id: str | None
    waiting_to_closeout: bool
    sprint_item_id: str | None
    sprint_item_title: str | None
    sprint_item_priority: str | None
    awaiting_reply: NotRequired[bool]
    awaiting_approval: NotRequired[bool]
    assigned: NotRequired[bool]
    agent_state: NotRequired[str]


class BoardSprintItem(TypedDict):
    """A Sprint Item's own identity and its supervisor's conversation.

    The shared attention projection adds the Item's own supervisor state and one rollup
    over its child Tickets after this database read.
    """

    id: str
    created_at: int
    conversation_id: str | None
    awaiting_reply: NotRequired[bool]
    awaiting_approval: NotRequired[bool]
    assigned: NotRequired[bool]
    agent_state: NotRequired[str]
    ticket_rollup: NotRequired[dict[str, object]]


@dataclass(frozen=True, slots=True)
class TicketListFilters:
    stages: tuple[str, ...] = ()
    excluded_stages: tuple[str, ...] = ()
    ticket_statuses: tuple[TicketStatus, ...] = ()
    excluded_ticket_statuses: tuple[TicketStatus, ...] = ()
    include_terminal: bool = False
    search: str | None = None


TicketFieldValues = Mapping[str, str]


@dataclass(frozen=True)
class PendingTicketProposal:
    """The one current result awaiting agreement on a Ticket."""

    field: str
    body: str
    proposed_by: str
    created_at: int


# --- the ceiling (§4.4.7) ---
NO_FURTHER: Final = "none"  # wire sentinel: ceiling = the newly entered Stage
# A ceiling id is any member of the type's ceiling_range (a str); "none" is the wire
# sentinel meaning "the newly entered Stage".
NextCeiling = str | Literal["none"]


# --- request bodies (§9 wire shapes) ---
# Most legacy bodies below are partial wire shapes: an absent key takes its documented
# default and unknown keys are ignored.


class CreateTicketBody(TypedDict, total=False):  # POST /tickets
    worker_type: Required[str]  # required registry type id (no ingress default)
    # A backend other than the Worker type's own brings its model with it: the type's
    # launch defaults belong to the type's backend, so there is nothing left for this
    # Ticket to run on unless the creator names one.
    employee_backend: str
    employee_launch_model: str
    title: str  # default ""
    kickoff_note: str  # default ""; proposed intake context / user guidance
    priority: str | None  # Explicit Priority value; omission resolves from placement
    deadline: str | None  # ISO date
    project: str | None  # legacy project name
    project_id: str | None
    sprint_id: str | None
    sprint_item_id: str | None
    blocked_by_ticket_ids: list[str]
    # Scope stated at creation by whoever has the authority to grant it. A creator that
    # states scope creates the Ticket already scoped. Omission keeps the default leash:
    # the kickoff parks for approval.
    ceiling: str | None


class TicketEdit(TypedDict, total=False):  # PATCH /tickets/{id}, parsed values
    """Every field on a Ticket that can be changed, and the only way to change one.

    An operation with a consequence of its own — propose, approve, reject, complete a
    user-owned gate, drop, delete, ask for help, choose what the Ticket launches on — is
    not here, and keeps its own route.
    """

    title: str
    priority: Priority
    deadline: str | None
    project_id: str | None
    sprint_id: str | None
    sprint_item_id: str | None
    recap: str
    guidance: str  # replaces the document
    guidance_append: str  # adds to it; naming both in one call is refused
    field_values: Mapping[str, str]  # settled values only, by field id
    ceiling: str


class ProposalBody(TypedDict, total=False):  # POST /tickets/{id}/propose
    body: str  # default ""


class AcceptBody(TypedDict, total=False):  # POST /tickets/{id}/accept/{field}
    edited_body: str | None  # direct edit applied before resolution
    next_ceiling: str | None  # Stage id or NO_FURTHER; the onward ceiling (§4.4.7)
    next_holder: object  # required full Principal for the next ceiling


class GateCompletionBody(TypedDict, total=False):  # POST /tickets/{id}/complete/{field}
    body: str  # default ""


class RejectionBody(TypedDict, total=False):  # POST /tickets/{id}/reject
    message: str | None  # optional guidance for the executing agent


class EmployeeConfigurationBody(TypedDict):
    # The complete launch configuration, and the model is named as surely as the backend:
    # a Ticket saved without one would launch its worker on whatever the backend picked
    # for itself. The reasoning effort may be null, because some models take none.
    employee_backend: str
    employee_launch_model: str
    employee_launch_reasoning_effort: str | None


@dataclass
class Ticket:  # §3.3 — column names match exactly
    id: str
    title: str  # <= TITLE_MAX_CHARS (200), every write path
    worker_type: str  # immutable registry id selected at creation
    employee_backend: str  # last-chosen backend for this Ticket's worker
    # The last-chosen model, and the reasoning effort that went with it. A null model is a
    # Ticket that has never chosen: the column was filled in back when leaving it empty
    # meant the backend's own model, and that is a value nobody picked, so such a Ticket
    # says nothing about what it runs on and its Worker type answers whole instead. A null
    # reasoning effort is a real answer — some models take none.
    employee_launch_model: str | None = field(default=None, kw_only=True)
    employee_launch_reasoning_effort: str | None = field(default=None, kw_only=True)
    stage: str  # directly stored Stage id
    priority: Priority  # default P3
    deadline: str | None  # ISO date
    project_id: str | None  # canonical Ticket Project placement
    project_name: str | None
    sprint_id: str | None  # canonical Ticket Sprint placement; NULL is backlog
    sprint_item_id: str | None
    effective_sprint_id: str | None  # compatibility alias for sprint_id
    resolved_priority_anchors: ResolvedTicketPriorityAnchors
    recap: str  # writable only past the type's first worker Stage
    guidance: str = field(default="", kw_only=True)  # durable instructions for the Ticket
    ceiling: str  # ceiling id; a member of the type's ceiling_range
    ceiling_holder: Principal = field(kw_only=True)
    ticket_status: TicketStatus  # durable state-of-control; transition functions write it
    # When ticket_status last actually changed, for display and elapsed-time facts.
    ticket_status_changed_at: int
    # Monotonic status-transition identity used by notifications and worker claims.
    # Unlike the timestamp, it cannot collide when two transitions share a second.
    ticket_status_revision: int
    conversation_id: str | None  # the Ticket's conversation link (column name is frozen)
    field_values: TicketFieldValues
    pending_proposal: PendingTicketProposal | None
    created_at: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class EmployeeLaunchConfiguration:
    """What the Ticket's worker last ran on: backend, model, reasoning effort.

    Kept up to date as the Ticket's conversation changes, so a fresh conversation
    starts from where the last one ended. The field names are the storage and wire
    names of the three columns and are frozen with them.

    The model is optional here for the one reason the column is: a Ticket written before a
    model had to be named has none. Nothing writes a null into it any more.
    """

    employee_backend: str
    employee_launch_model: str | None
    employee_launch_reasoning_effort: str | None


@dataclass(frozen=True)
class EmployeeSessionIdTransition:
    expected_conversation_id: str | None
    candidate_conversation_id: str


@dataclass(frozen=True)
class TicketDeletion:
    """The deleted identity plus surviving resources affected by the transaction."""

    ticket_id: str
    title: str
    day_ids: tuple[str, ...]
    sprint_item_ids: tuple[str, ...]
    sprint_ids: tuple[str, ...]
    linked_ticket_ids: tuple[str, ...]
