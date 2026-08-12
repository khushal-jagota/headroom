"""Contracts for durable Sprint Item supervisor obligations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class SupervisorObligationKind(StrEnum):
    agent_review = "agent_review"
    user_review = "user_review"
    blocked = "blocked"
    worker_failure = "worker_failure"
    needs_user = "needs_user"
    completed = "completed"


class SupervisorObligationLifecycle(StrEnum):
    pending = "pending"
    delivered = "delivered"
    acknowledged = "acknowledged"
    resolved = "resolved"
    superseded = "superseded"
    failed = "failed"


@dataclass(frozen=True, slots=True)
class SupervisorObligation:
    id: str
    sprint_item_id: str
    ticket_id: str
    kind: SupervisorObligationKind
    source_identity: str
    lifecycle: SupervisorObligationLifecycle
    delivery_id: str | None
    attempt_count: int
    retry_at: int | None
    last_error: str | None
    created_at: int
    updated_at: int
    acknowledged_at: int | None


@dataclass(frozen=True, slots=True)
class SupervisorDelivery:
    id: str
    sprint_item_id: str
    sender_message_id: str
    conversation_id: str | None
    state: str
    created_at: int
    updated_at: int
