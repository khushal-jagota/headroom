"""Version 1 durable conversation facts and synchronous transaction participation.

These facts describe knowledge, never a restored process, callback, or held message.
An intent proves only that Panels prepared an external attempt. Unknown attempts must
never be retried by recovery. Public delivery receipts retain their existing contract.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

LIFECYCLE_CONTRACT_VERSION = 1


class LifecycleKind(StrEnum):
    turn = "turn"
    attempt = "attempt"
    request = "request"


class LifecycleOperation(StrEnum):
    turn = "turn"
    prompt = "prompt"
    steer = "steer"
    permission = "permission"
    user_input = "user_input"
    permission_answer = "permission_answer"
    user_input_answer = "user_input_answer"
    cancel = "cancel"


class LifecycleStatus(StrEnum):
    intent = "intent"
    running = "running"
    pending = "pending"
    answering = "answering"
    started = "started"
    injected = "injected"
    accepted = "accepted"
    refused = "refused"
    unknown = "unknown"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"
    unavailable = "unavailable"
    answered = "answered"


@dataclass(frozen=True, slots=True)
class LifecycleFact:
    kind: LifecycleKind
    identity: str
    turn_number: int
    operation: LifecycleOperation
    status: LifecycleStatus
    provider_identity: str | None = None


@dataclass(frozen=True, slots=True)
class LifecycleTransition:
    """Candidate post-transition facts, visible within the uncommitted transaction."""

    conversation_id: str
    facts: tuple[LifecycleFact, ...]
    receipt_sequences: tuple[int, ...] = ()
    contract_version: int = LIFECYCLE_CONTRACT_VERSION


class ConversationTransactionParticipant(Protocol):
    def __call__(self, connection: sqlite3.Connection, candidate: LifecycleTransition, /) -> None:
        """Participate synchronously on this connection. Never commit or call providers.

        A failure rolls back receipts and facts together. The candidate is not published
        state. The caller owns commit, and publication follows successful commit only.
        """
        ...
