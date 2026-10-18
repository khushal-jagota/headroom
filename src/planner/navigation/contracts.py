"""Generic navigation v3. Signal-derived semantics remain explicitly unsupported."""

from __future__ import annotations

from typing import Literal, TypedDict

SCHEMA_VERSION = 3
SUPPORTED_SEMANTICS = "generic-source-facts-v3"
RecordKind = Literal[
    "ticket", "orchestrator", "chief", "conversation", "address", "totals", "project"
]


class RecordKey(TypedDict):
    kind: RecordKind
    id: str


class PrincipalValue(TypedDict):
    kind: str
    id: str


class TicketValue(TypedDict):
    title: str
    worker_type: str
    stage: str
    ticket_status: str
    blocked: bool
    updated_at: int
    worker_step_claim_out: bool
    priority: str
    deadline: str | None
    project_id: str | None
    orchestrator_kind: str
    sprint_item_id: str | None
    conversation_id: str | None
    is_backlogged: bool
    ceiling: str
    ceiling_holder: PrincipalValue
    proposal_field: str | None
    proposal_revision: int
    proposal_created_at: int | None
    stage_ownership: str | None
    terminal: bool
    locale: str


class OrchestratorValue(TypedDict):
    title: str
    priority: str
    created_at: int
    project_id: str
    settled: bool
    conversation_id: str | None


class ChiefValue(TypedDict):
    label: str
    conversation_id: str | None


class RawTurnValue(TypedDict):
    identity: str
    turn_number: int
    status: str


class TurnStatusCounts(TypedDict):
    intent: int
    running: int


class RequestStatusCounts(TypedDict):
    pending: int
    answering: int


class AttemptStatusCounts(TypedDict):
    intent: int
    unknown: int


class LifecycleSummary(TypedDict):
    latest_turn: RawTurnValue | None
    turn_status_counts: TurnStatusCounts
    request_status_counts: RequestStatusCounts
    attempt_status_counts: AttemptStatusCounts


class ConversationValue(TypedDict):
    latest_sequence: int
    owner_read_through_sequence: int
    latest_agent_activity_sequence: int
    latest_agent_activity_at: int | None
    error_acknowledged_through_sequence: int
    lifecycle_summary: LifecycleSummary


class AddressValue(TypedDict):
    owner_kind: Literal["ticket", "orchestrator", "chief"]
    owner_id: str
    conversation_id: str
    current: bool
    parent_kind: Literal["orchestrator", "chief"] | None
    parent_id: str | None


class TotalsValue(TypedDict):
    child_count: int
    distinct_conversation_count: int


class ProjectValue(TypedDict):
    name: str
    priority: str | None


class TicketRecord(TypedDict):
    kind: Literal["ticket"]
    id: str
    value: TicketValue


class OrchestratorRecord(TypedDict):
    kind: Literal["orchestrator"]
    id: str
    value: OrchestratorValue


class ChiefRecord(TypedDict):
    kind: Literal["chief"]
    id: str
    value: ChiefValue


class ConversationRecord(TypedDict):
    kind: Literal["conversation"]
    id: str
    value: ConversationValue


class AddressRecord(TypedDict):
    kind: Literal["address"]
    id: str
    value: AddressValue


class TotalsRecord(TypedDict):
    kind: Literal["totals"]
    id: str
    value: TotalsValue


class ProjectRecord(TypedDict):
    kind: Literal["project"]
    id: str
    value: ProjectValue


type NavigationRecord = (
    TicketRecord
    | OrchestratorRecord
    | ChiefRecord
    | ConversationRecord
    | AddressRecord
    | TotalsRecord
    | ProjectRecord
)


class Continuity(TypedDict):
    scope: str
    schema_version: int
    semantics: Literal["generic-source-facts-v3"]
    epoch: str
    revision: str


class Snapshot(Continuity):
    records: list[NavigationRecord]


class RevisionBatch(TypedDict):
    scope: str
    schema_version: int
    semantics: Literal["generic-source-facts-v3"]
    epoch: str
    base_revision: str
    revision: str
    upserts: list[NavigationRecord]
    deletes: list[RecordKey]


class JournalPage(Continuity):
    batches: list[RevisionBatch]
    has_more: bool


class HistoryUnavailable(ValueError):
    """The cursor belongs to another epoch, future revision, or missing history."""


def parse_revision(value: str) -> int:
    """Accept only canonical, nonnegative decimal revision strings."""
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError("revision must be a canonical decimal string")
    revision = int(value)
    if str(revision) != value:
        raise ValueError("revision must be a canonical decimal string")
    return revision
