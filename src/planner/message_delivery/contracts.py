"""Contracts for the general Send Message command and its API."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.conversation.contracts import PromptDeliveryFate


class MessageDeliveryMode(StrEnum):
    """The public Send Message choices."""

    queue = "queue"
    steer = "steer"


class MessageTargetType(StrEnum):
    chief = "chief"
    ticket = "ticket"
    sprint_item = "sprint_item"
    agent = "agent"


@dataclass(frozen=True, slots=True)
class MessageTarget:
    target_type: MessageTargetType
    target_id: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedMessageDestination:
    destination_type: str
    destination_id: str


@dataclass(frozen=True, slots=True)
class MessageDeliveryResult:
    target: MessageTarget
    resolved_destination: ResolvedMessageDestination
    conversation_id: str | None
    fate: PromptDeliveryFate
