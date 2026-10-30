"""Typed interpretation and settlement values for backend events."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

from pydantic import JsonValue

from planner.conversation.backends.contracts import BackendChild, TurnToken
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationEventPayload,
    PermissionAskedEventPayload,
    UserInputRequestedEventPayload,
)
from planner.conversation.lifecycle import LifecycleFact
from planner.core.contracts import Principal

if TYPE_CHECKING:
    from planner.conversation.storage import StoredConversationEvent


def child_stops_outside_lock(child: BackendChild | None) -> TypeGuard[BackendChild]:
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


# The participant calls this only after replay and admission checks. Neither function
# commits; a refusal must report taken=False after rolling its savepoint back.
type EventTaken = Callable[[], dict[str, JsonValue]]


@dataclass(frozen=True, slots=True)
class AcceptedEvent:
    """A participant's result and whether its effect survived acceptance.

    Replays and refusals return taken=False even if a savepoint rolled back a write.
    The conversation does not inspect the participant's opaque result.
    """

    result: dict[str, JsonValue]
    taken: bool
    written: tuple[StoredConversationEvent, ...] = ()


type CommitParticipant = Callable[[sqlite3.Connection, EventTaken], AcceptedEvent]
