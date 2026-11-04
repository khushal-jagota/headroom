"""Contracts for the general Send Message command and its API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from planner.conversation.contracts import PromptDeliveryFate
from planner.core.contracts import Principal


class MessageDeliveryMode(StrEnum):
    """The public Send Message choices."""

    queue = "queue"
    steer = "steer"
    send_now = "send_now"


@dataclass(frozen=True, slots=True)
class MessageRecordedToOwner:
    """The addressed row landed and no backend received it."""


@dataclass(frozen=True, slots=True)
class MessageDeliveryResult:
    recipient: Principal
    conversation_id: str | None
    fate: PromptDeliveryFate | MessageRecordedToOwner


class MessageAdmission(Protocol):
    """Attachment admission under the existing destination lock, before link mutation."""

    async def preflight(
        self, current_conversation_id: str | None
    ) -> MessageDeliveryResult | None: ...

    def finish(self, fate: PromptDeliveryFate) -> None: ...

    def abort_before_dispatch(self) -> None: ...
