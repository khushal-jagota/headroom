"""Stored shapes for durable notices to responsible Panels principals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.core.contracts import Principal


class WakeSourceKind(StrEnum):
    proposal = "proposal"
    worker_error = "worker_error"
    turn_failure = "turn_failure"


class WakeBatchStatus(StrEnum):
    pending = "pending"
    offering = "offering"
    dispatching = "dispatching"
    accepted = "accepted"
    uncertain = "uncertain"
    refused = "refused"
    discarded = "discarded"
    delivered = "delivered"


@dataclass(frozen=True, slots=True)
class WakeBatch:
    id: int
    sprint_item_id: str
    target: Principal
    sender_message_id: str
    message: str
    status: WakeBatchStatus
    conversation_id: str | None
    process_token: str | None
