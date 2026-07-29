"""Contracts shared by notification sources, policy, APIs, and delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class NotificationType:
    id: str
    label: str
    description: str
    default_enabled: bool


NOTIFICATION_TYPES: Final[tuple[NotificationType, ...]] = (
    NotificationType(
        "ticket_needs_approval",
        "Needs approval",
        "A Ticket has a proposal waiting for your decision.",
        True,
    ),
    NotificationType(
        "ticket_needs_input",
        "Needs input",
        "A Ticket or worker is waiting for information from you.",
        True,
    ),
    NotificationType(
        "permission_requested",
        "Permission request",
        "A running worker is waiting for permission.",
        True,
    ),
    NotificationType(
        "worker_completed",
        "Worker completed",
        "A worker turn completed and its reply is ready.",
        True,
    ),
    NotificationType(
        "worker_failed",
        "Worker failed",
        "A worker turn or Ticket failed and needs attention.",
        True,
    ),
)
NOTIFICATION_TYPE_BY_ID: Final[dict[str, NotificationType]] = {
    item.id: item for item in NOTIFICATION_TYPES
}


@dataclass(frozen=True, slots=True)
class NotificationFact:
    fact_id: str
    notification_type: str
    ticket_id: str
    ticket_title: str
    occurred_at: int


@dataclass(frozen=True, slots=True)
class NotificationIntent:
    fact_id: str
    title: str
    body: str
    route: str
    tag: str


@dataclass(frozen=True, slots=True)
class PushSubscription:
    subscription_id: str
    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True, slots=True)
class PendingDelivery:
    fact_id: str
    subscription: PushSubscription
    intent: NotificationIntent
    attempts: int


@dataclass(frozen=True, slots=True)
class WebPushIdentity:
    private_key: str
    public_key: str


@dataclass(frozen=True, slots=True)
class WebPushResult:
    delivered: bool
    expired: bool = False
    error: str | None = None
