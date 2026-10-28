"""Typed interpretation and settlement values for backend events."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from planner.conversation.backends.contracts import TurnToken
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationEventPayload,
    PermissionAskedEventPayload,
    UserInputRequestedEventPayload,
)
from planner.conversation.lifecycle import LifecycleFact
from planner.core.contracts import Principal
from planner.executor.contracts import AcceptanceReceipt, JsonObject, QueuedEnvelope

if TYPE_CHECKING:
    from planner.conversation.storage import StoredConversationEvent


@dataclass(frozen=True, slots=True)
class InterpretedRequest:
    payload: PermissionAskedEventPayload | UserInputRequestedEventPayload
    lifecycle: LifecycleFact
    provider_identity: str


@dataclass(frozen=True, slots=True)
class TurnEndingContext:
    token: TurnToken
    automatic_compaction: bool
    compaction_confirmed: bool
    automatic_notice: bool
    prompt_senders: tuple[Principal, ...]
    explicit_reply_recipients: tuple[Principal, ...]


@dataclass(frozen=True, slots=True)
class InterpretedTurnEnding:
    payloads: tuple[ConversationEventPayload, ...]
    lifecycle: tuple[LifecycleFact, ...]
    compaction_result: AutomaticCompactionResult | None
    failure_notice: bool


@dataclass(frozen=True, slots=True)
class ExecutorEffectResult:
    receipt: AcceptanceReceipt
    written: tuple[StoredConversationEvent, ...]
    fresh: bool


type ExecutorEffectWriter = Callable[
    [sqlite3.Connection, QueuedEnvelope], tuple[JsonObject, tuple[StoredConversationEvent, ...]]
]
