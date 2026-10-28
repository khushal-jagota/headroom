"""Ticket-owned domain shapes: fields, scope, requests, and stored Ticket rows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Literal, NotRequired, Required, TypedDict

from planner.conversation.contracts import ConversationSignal
from planner.core.contracts import JsonDict, Principal

# §3.3 ticket title length cap. The DDL carries the matching literal
# `CHECK (length(title) <= 200)` as the DB-level backstop; this constant is the
# single enforcement source the write paths pass to admission.validate_title.
TITLE_MAX_CHARS: Final = 200


class TicketLocale(StrEnum):
    vps = "vps"
    macos = "macos"


@dataclass(frozen=True, slots=True)
class ResolvedTicketCreationContext:
    locale: TicketLocale
    launch_configuration: EmployeeLaunchConfiguration
    project_id: str | None


class TicketLocaleChoice(TypedDict):
    locale: str
    compatible: bool
    reason: str | None


class TicketCreationContextRead(TypedDict):
    resolved_locale: str
    employee_backend: str
    project_id: str | None
    locale_choices: list[TicketLocaleChoice]
    launch_validation: NotRequired[JsonDict]


class StageOwnershipMode(StrEnum):
    worker = "worker"
    user = "user"


class WorkerStepClaim(StrEnum):
    """The one state-of-control fact a Ticket stores, written by data-layer transitions.

    ``out`` means the wakeup system has sent this Ticket's worker its step. It says
    nothing about whether a turn is live right now: that is a conversation fact.
    """

    none = "none"
    out = "out"
    errored = "errored"


class TicketStatus(StrEnum):  # derived at the moment of a read; never stored
    empty = "empty"
    blocked = "blocked"  # what rest is called while a live blocker exists
    agent = "agent"
    awaiting_approval = "awaiting_approval"
    errored = "errored"


class WorkAttention(TypedDict):
    """The shared owner-attention and employee-activity projection."""

    awaiting_reply: bool
    awaiting_answer: bool
    awaiting_approval: bool
    awaiting_agent_approval: bool
    assigned: bool
    agent_state: str


class BoardCardSource(TypedDict):
    """The stored Ticket projection before live work attention is attached."""

    id: str
    title: str
    recap: str
    recap_preview: str
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
    locale: str
    stage: str
    stage_label: str
    gating_field: str | None
    gating_field_label: str | None
    is_done: bool
    is_backlogged: bool
    blocked: bool
    conversation_id: str | None
    waiting_to_closeout: bool
    manager_id: str | None
    manager_title: str | None
    manager: JsonDict


class BoardCard(BoardCardSource, WorkAttention):
    """The complete Ticket projection returned to the Workspace rail."""

    conversation_signal: ConversationSignal


class BoardManagerSource(TypedDict):
    """A represented Manager and its complete children before live attention.

    The shared attention projection adds each child's facts, the Item's own Manager
    state, and a separate child rollup after this database read.
    """

    id: str
    title: str
    recap: str
    recap_preview: str
    project_id: str
    project: str
    created_at: int
    conversation_id: str | None
    tickets: list[JsonDict]
    settled: bool


class BoardManager(BoardManagerSource, WorkAttention):
    """The complete Manager projection returned to the Workspace rail."""

    conversation_signal: ConversationSignal
    ticket_rollup: WorkAttention


@dataclass(frozen=True, slots=True)
class TicketListFilters:
    manager: Principal | None = None
    stages: tuple[str, ...] = ()
    excluded_stages: tuple[str, ...] = ()
    ticket_statuses: tuple[TicketStatus, ...] = ()
    excluded_ticket_statuses: tuple[TicketStatus, ...] = ()
    include_terminal: bool = False
    search: str | None = None
    is_backlogged: bool | None = None
    worker_types: tuple[str, ...] = ()


TicketFieldValues = Mapping[str, str]


class TicketApprovalMethod(StrEnum):
    explicit_accept = "explicit_accept"
    auto_accept = "auto_accept"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class TicketFieldApproval:
    """Approval of the current field text, with a readable identity snapshot."""

    method: TicketApprovalMethod
    principal: Principal | None
    display_label: str


TicketFieldApprovals = Mapping[str, TicketFieldApproval]


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
    """Accepted create keys. Unknown keys are rejected before creation."""

    worker_type: Required[str]  # required registry type id (no ingress default)
    # A backend other than the Worker type's own brings its model with it: the type's
    # launch defaults belong to the type's backend, so there is nothing left for this
    # Ticket to run on unless the creator names one.
    employee_backend: str
    employee_launch_model: str
    # Null or omission keeps the existing defaults. A nonblank explicit effort must
    # validate against the resolved model before creation, even for direct callers.
    employee_launch_reasoning_effort: str | None
    selection_kind: str
    locale: str
    title: str  # default ""
    brief: str  # default ""; intake text for the Brief field
    deadline: str | None  # ISO date
    project: str | None  # legacy project name
    project_id: str | None
    manager: object  # explicit chief or manager Principal reference
    blocked_by_ticket_ids: list[str]
    # Scope stated at creation by whoever has the authority to grant it. A creator that
    # states scope creates the Ticket already scoped. Omission keeps the default leash:
    # the kickoff parks for approval.
    ceiling: str | None
    # Who the ceiling is held for. Absent, the creator holds it, as before. A creator can
    # name any holder here without holding anything first: this is how a Ticket is opened
    # for somebody else to review.
    ceiling_holder: object  # full Principal


class TicketCreationReceipt(TypedDict):
    """Creation-only explanation, added to the ordinary Ticket response fields.

    Normal CLI output and JSON consumers receive the same plain-language receipt.
    It describes the grant, remaining autonomous work, and first review destination.
    """

    supervision_receipt: str


class TicketEdit(TypedDict, total=False):  # PATCH /tickets/{id}, parsed values
    """Every field on a Ticket that can be changed, and the only way to change one.

    An operation with a consequence of its own — propose, approve, reject, complete a
    user-owned gate, drop, delete, ask for help, choose what the Ticket launches on — is
    not here, and keeps its own route.
    """

    title: str
    deadline: str | None
    project_id: str | None
    manager_id: str | None
    manager: Principal
    recap: str
    field_values: Mapping[str, str]  # settled values only, by field id
    ceiling: str  # pending same/lower scope is editable; raising past the stage uses accept
    ceiling_holder: Principal  # who is asked; allowed while a proposal is parked


class ProposalBody(TypedDict, total=False):  # POST /tickets/{id}/propose
    body: str  # default ""


class AcceptBody(TypedDict, total=False):  # POST /tickets/{id}/accept/{field}
    expected_proposal_revision: int | None  # visible proposal identity for approval gestures
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
    selection_kind: NotRequired[str]


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
    deadline: str | None  # ISO date
    project_id: str | None  # effective Project placement, derived from the parent when present
    project_name: str | None
    manager_id: str | None
    manager_kind: str = field(default="chief", kw_only=True)
    manager_title: str | None
    recap: str  # writable only past the type's first worker Stage
    ceiling: str  # ceiling id; a member of the type's ceiling_range
    # An address, not an authority. It says which principal a parked proposal is for, so
    # that Review, attention and notifications can put it in front of them. It says
    # nothing about who may accept or reject it; the one rule answers that.
    ceiling_holder: Principal = field(kw_only=True)
    # Derived when the row is read, never stored. Kept on the Ticket because almost
    # every reader wants the answer, not the facts behind it.
    ticket_status: TicketStatus
    # The one stored state-of-control fact: whether this Ticket's worker step is out.
    worker_step_claim: WorkerStepClaim
    # Monotonic claim-transition identity used by notifications and worker claims.
    # Unlike the timestamp, it cannot collide when two transitions share a second.
    worker_step_claim_revision: int
    conversation_id: str | None  # the Ticket's conversation link (column name is frozen)
    field_values: TicketFieldValues
    field_approvals: TicketFieldApprovals = field(default_factory=dict, kw_only=True)
    pending_proposal: PendingTicketProposal | None
    created_at: int
    updated_at: int
    # Monotonic proposal identity. Proposal timestamps have whole-second precision, so
    # readers use this when they must distinguish a replacement filed in the same second.
    locale: TicketLocale = field(default=TicketLocale.vps, kw_only=True)
    is_backlogged: bool = field(default=False, kw_only=True)
    pending_proposal_revision: int = field(default=0, kw_only=True)


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
    manager_ids: tuple[str, ...]
    linked_ticket_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewTransferNotice:
    """An in-memory courtesy notice, exposed only after its write commits."""

    ticket_id: str
    ticket_title: str
    previous_holder: Principal
    new_holder: Principal
    new_holder_name: str
    notify_previous_holder: bool
    mover_name: str | None
