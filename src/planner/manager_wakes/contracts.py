"""Stored shapes for durable Sprint Item manager wakes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class WakeSourceKind(StrEnum):
    proposal = "proposal"
    worker_error = "worker_error"


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
    sender_message_id: str
    message: str
    status: WakeBatchStatus
    conversation_id: str | None
    process_token: str | None
