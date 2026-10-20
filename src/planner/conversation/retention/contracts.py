"""Frozen inputs and reviewable outputs for explicit diagnostic maintenance."""

from __future__ import annotations

from dataclasses import dataclass

from planner.conversation.lifecycle import LifecycleFact


@dataclass(frozen=True, slots=True)
class RetentionEvent:
    conversation_id: str
    sequence: int
    kind: str
    payload: str
    created_at: int


@dataclass(frozen=True, slots=True)
class TicketOwner:
    ticket_id: str
    stage: str
    worker_step_claim: str


@dataclass(frozen=True, slots=True)
class OrchestratorOwner:
    orchestrator_id: str
    settled: bool


@dataclass(frozen=True, slots=True)
class ConversationRetentionInput:
    conversation_id: str
    identity_json: str
    tickets: tuple[TicketOwner, ...]
    orchestrators: tuple[OrchestratorOwner, ...]
    agent_keys: tuple[str, ...]
    events: tuple[RetentionEvent, ...]
    protected_sequences: tuple[int, ...]
    owner_read_through_sequence: int
    error_acknowledged_through_sequence: int
    linked_ticket_ids: tuple[str, ...]
    lifecycle_facts: tuple[LifecycleFact, ...]


@dataclass(frozen=True, slots=True)
class RemovalCandidate:
    conversation_id: str
    sequence: int
    kind: str
    created_at: int
    payload_bytes: int
    row_sha256: str


@dataclass(frozen=True, slots=True)
class ConversationRetentionDecision:
    conversation_id: str
    hold_reasons: tuple[str, ...]
    candidates: tuple[RemovalCandidate, ...]
    total_event_count: int


@dataclass(frozen=True, slots=True)
class RetentionReport:
    policy_version: str
    database_path: str
    database_identity: str
    schema_fingerprint: str
    source_fingerprint: str
    as_of_unix: int
    cutoff_unix: int
    decisions: tuple[ConversationRetentionDecision, ...]
    command: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExpiredSequenceRange:
    conversation_id: str
    first_sequence: int
    last_sequence: int
    cutoff_unix: int
    expired_at_unix: int
    event_count: int


@dataclass(frozen=True, slots=True)
class ApplyResult:
    removed_event_count: int
    removed_payload_bytes: int
    affected_conversation_count: int
    expired_at_unix: int


@dataclass(frozen=True, slots=True)
class ReclaimResult:
    database_bytes_before: int
    database_bytes_after: int
    allocated_bytes_before: int
    allocated_bytes_after: int
    wal_bytes_after: int
