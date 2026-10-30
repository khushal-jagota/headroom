"""Typed interpretation and settlement values for backend events."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeGuard

from planner.conversation.backends.contracts import BackendChild, TurnToken
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationEventPayload,
    PermissionAskedEventPayload,
    UserInputRequestedEventPayload,
)
from planner.conversation.lifecycle import LifecycleFact
from planner.core.contracts import Principal
from planner.execution_identity import ChildReference
from planner.executor.protocol import AcceptanceReceipt, ChildCommand, JsonObject, QueuedEnvelope

if TYPE_CHECKING:
    from planner.conversation.storage import StoredConversationEvent


class RetainedDeliveryChild(BackendChild, Protocol):
    """Existing turn-table participation, pending the separate run-table split.

    Children whose stop needs callbacks expose this retained delivery state. The
    conversation still persists it atomically with lifecycle facts; PR 15 moves
    that ownership to Mac runs. It is not part of the neutral child interface.
    """

    reference: ChildReference
    assignment_principal: Principal | None
    initial_commands: list[ChildCommand]
    record_command: Callable[[ChildCommand], Awaitable[None]] | None
    prompt_accepted: bool


def child_stops_outside_lock(child: BackendChild | None) -> TypeGuard[RetainedDeliveryChild]:
    return child is not None and child.stop_outside_lock


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
