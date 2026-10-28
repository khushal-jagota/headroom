"""Stored shapes for durable notices to responsible Panels principals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.core.contracts import Principal


class WakeSourceKind(StrEnum):
    proposal = "proposal"
    worker_error = "worker_error"
    turn_failure = "turn_failure"
    launch_validation = "launch_validation"


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
    manager_id: str
    target: Principal
    sender_message_id: str
    message: str
    status: WakeBatchStatus
    conversation_id: str | None
    process_token: str | None


class ProposalNoticeSuppressionReason(StrEnum):
    ticket_missing = "ticket_missing"
    proposal_resolved = "proposal_resolved"
    revision_changed = "revision_changed"
    reviewer_changed = "reviewer_changed"


@dataclass(frozen=True, slots=True)
class ProposalNoticeState:
    """Current canonical review facts from the admission transaction."""

    ticket_exists: bool
    pending_revision: int | None
    reviewer: Principal | None


@dataclass(frozen=True, slots=True)
class ProposalNoticeSuppression:
    """Facts retained for each source that final admission or batching suppresses."""

    wake_id: int
    ticket_id: str | None
    source_revision: int
    target: Principal
    current: ProposalNoticeState
    reason: ProposalNoticeSuppressionReason
