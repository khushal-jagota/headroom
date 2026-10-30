"""Bounded navigation views over the existing canonical source and proof domain.

This is wire 3, not a filtered wire-2 replica. Source continuity stays canonical.
All fields are generated into the existing browser contract module.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from planner.navigation.contracts import (
    AddressRecord,
    AttentionKnowledge,
    ChiefRecord,
    Continuity,
    ManagerValue,
    ProjectRecord,
    RecordKey,
    TicketValue,
)
from planner.navigation.delivery_contracts import CurrentAuthority, DegradedAuthority
from planner.navigation.proof_contracts import ProofResult

VIEW_WIRE_VERSION = 3
VIEW_PROJECTION_VERSION = 7

ViewSection = Literal["rail_parents", "rail_tickets", "children", "settled", "unread", "review"]
ViewGroup = Literal[
    "all",
    "awaiting_approval",
    "awaiting_answer",
    "assigned",
    "awaiting_reply",
    "errored",
    "backlog",
    "agent",
    "waiting_to_closeout",
    "status_awaiting_approval",
    "empty",
    "blocked",
    "done",
]


class ViewOwner(TypedDict):
    kind: Literal["chief", "manager", "ticket"]
    id: str


class ViewWindowRequest(TypedDict):
    """Server-defined membership, never an arbitrary client predicate.

    Null anchor starts the section. The opaque anchor binds section, owner,
    group, ordering generation and complete sort tuple. Deleted anchors still
    work as keyset boundaries. Limits count requested rows, not dependencies.
    A zero limit requests the exact count without member records.
    """

    section: ViewSection
    owner: ViewOwner | None
    group: ViewGroup
    child_filter: Literal["all", "active", "backlogged", "done"]
    anchor: str | None
    direction: Literal["after", "before"]
    limit: int


class ReviewHistoryKey(TypedDict):
    kind: Literal["ticket", "conversation"]
    id: str


class ReviewSummary(TypedDict):
    skipped: int
    new: int


class ViewRequest(TypedDict):
    projection_version: Literal[7]
    scope_generation: int
    locale: str
    selected: ViewOwner | None
    selected_conversation_id: str | None
    # Exact painted Review conversations whose canonical owners must stay visible
    # while a bounded reply outcome resolves, even after unread membership clears.
    review_conversation_ids: list[str]
    review_skipped_keys: list[ReviewHistoryKey]
    review_priority_keys: list[ReviewHistoryKey]
    windows: list[ViewWindowRequest]


class ViewContentReference(TypedDict):
    """Immutable recap identity. Versioned body reads refuse a changed source."""

    owner: ViewOwner
    version: str
    utf8_bytes: int


class ViewTicketValue(TicketValue):
    # recap is the complete body when reference is null, otherwise its preview.
    # Exact control identity is unchanged. Canonical saved text is never truncated.
    recap_reference: ViewContentReference | None


class ViewManagerValue(ManagerValue):
    recap_reference: ViewContentReference | None


class ViewSettledManagerValue(TypedDict):
    """Settled rail membership without full-surface content dependencies."""

    title: str
    created_at: int
    updated_at: int
    settled: Literal[True]
    conversation_id: str | None


class ViewConversationValue(TypedDict):
    first_unread_owner_message_at: int | None
    owner_read_through_sequence: int
    error_acknowledged_through_sequence: int
    attention: AttentionKnowledge


class ViewTicketRecord(TypedDict):
    kind: Literal["ticket"]
    id: str
    value: ViewTicketValue


class ViewManagerRecord(TypedDict):
    kind: Literal["manager"]
    id: str
    value: ViewManagerValue


class ViewSettledManagerRecord(TypedDict):
    kind: Literal["manager"]
    id: str
    value: ViewSettledManagerValue


class ViewConversationRecord(TypedDict):
    kind: Literal["conversation"]
    id: str
    value: ViewConversationValue


type ViewRecord = (
    ViewTicketRecord
    | ViewManagerRecord
    | ViewSettledManagerRecord
    | ViewConversationRecord
    | ChiefRecord
    | AddressRecord
    | ProjectRecord
)


class ViewWindow(TypedDict):
    """Replace a whole window atomically; never infer a global total from members."""

    window_id: str
    request: ViewWindowRequest
    members: list[RecordKey]
    total: int
    previous: str | None
    next: str | None
    forward_overlap: str | None
    backward_overlap: str | None


class ViewCounts(TypedDict):
    running_conversations: int | None
    out_claims: int
    review: int
    unread: int
    settled: int


class ReviewConversationContext(TypedDict):
    conversation_id: str
    canonical_address_id: str | None


class ViewContent(TypedDict):
    records: list[ViewRecord]
    windows: list[ViewWindow]
    counts: ViewCounts
    review_context: list[ReviewConversationContext]
    review_summary: ReviewSummary


class ViewIdentity(TypedDict):
    wire_version: Literal[3]
    projection_version: Literal[7]
    view_id: str
    scope_generation: int
    ordering_generation: str
    coverage: Continuity
    content_version: str


class CurrentViewSnapshot(ViewIdentity):
    event: Literal["snapshot"]
    mode: Literal["current"]
    authority: CurrentAuthority
    content: ViewContent
    proof: ProofResult | None


class DegradedViewSnapshot(ViewIdentity):
    event: Literal["snapshot"]
    mode: Literal["degraded"]
    authority: DegradedAuthority
    content: ViewContent
    proof: None


type ViewSnapshot = CurrentViewSnapshot | DegradedViewSnapshot


class ViewPatch(ViewIdentity):
    event: Literal["patch"]
    authority: CurrentAuthority
    previous_coverage: Continuity
    previous_content_version: str
    upserts: list[ViewRecord]
    evictions: list[RecordKey]
    # Full bounded window/count metadata, even if only one member changed.
    # Eviction is absence from this scope, never a claim of canonical deletion.
    windows: list[ViewWindow]
    counts: ViewCounts
    review_context: list[ReviewConversationContext]
    review_summary: ReviewSummary


class ViewCheckpoint(ViewIdentity):
    event: Literal["checkpoint"]
    authority: CurrentAuthority
    previous_coverage: Continuity
    # content_version equals the previously admitted version. There are no rows.


class ViewReset(TypedDict):
    event: Literal["reset"]
    wire_version: Literal[3]
    view_id: str
    scope_generation: int
    reason: Literal["authority_changed", "source_changed", "overflow", "view_unavailable"]


type ViewFrame = ViewSnapshot | ViewPatch | ViewCheckpoint | ViewReset
