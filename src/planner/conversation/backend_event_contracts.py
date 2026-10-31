"""Typed interpretation and settlement values for backend events."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeGuard

from pydantic import JsonValue

from planner.conversation.backends.contracts import BackendChild, TurnToken
from planner.conversation.events import (
    AutomaticCompactionResult,
    ConversationEventPayload,
    PermissionAskedEventPayload,
    UserInputQuestion,
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


@dataclass(slots=True)
class RunningTurn:
    token: TurnToken
    automatic_compaction: bool = False
    compaction_confirmed: bool = False
    pending_permission_ask_ids: set[str] = field(default_factory=set)
    permission_provider_ids: dict[str, str] = field(default_factory=dict)
    user_input_provider_ids: dict[str, str] = field(default_factory=dict)
    pending_user_input_questions: dict[str, tuple[UserInputQuestion, ...]] = field(
        default_factory=dict
    )
    prompt_senders: dict[Principal, None] = field(default_factory=dict)
    explicit_reply_recipients: set[Principal] = field(default_factory=set)
    automatic_notice: bool = False
    ended: bool = False
    callbacks_revoked: bool = False
    # Set when the core is part-way through ending this turn itself — an interrupt, or a
    # send-now killing the incumbent. The cancel goes out with the lock let go, and the
    # backend reports the ending it was just asked for while that is happening. Only one
    # of the two may be recorded, and it is the core's: the core is the side that knows
    # the turn was interrupted rather than merely over.
    ending_is_the_cores: bool = False


@dataclass(frozen=True, slots=True)
class InterpretedBackendEvent:
    payloads: tuple[ConversationEventPayload, ...] = ()
    lifecycle: tuple[LifecycleFact, ...] = ()
    predicted: RunningTurn | None = None
    compacted: bool = False
    compaction_result: AutomaticCompactionResult | None = None
    failure_notice: bool = False
