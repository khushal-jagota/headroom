"""Contracts for the general Send Message command and its API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.conversation.contracts import PromptDeliveryFate
from planner.core.contracts import Principal


class MessageDeliveryMode(StrEnum):
    """The public Send Message choices."""

    queue = "queue"
    steer = "steer"


@dataclass(frozen=True, slots=True)
class MessageDeliveryResult:
    recipient: Principal
    conversation_id: str | None
    fate: PromptDeliveryFate
