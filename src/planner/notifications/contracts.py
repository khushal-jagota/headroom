"""Contracts shared by notification sources, policy, APIs, and delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

NotificationSubjectKind = Literal["ticket", "agent", "sprint_item"]
TICKET_NOTIFICATION_SUBJECT_KEY: Final = "tickets"
SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY: Final = "sprint_item_supervisors"


@dataclass(frozen=True, slots=True)
class NotificationType:
    id: str
    label: str
    description: str
    default_enabled: bool


@dataclass(frozen=True, slots=True)
class NotificationSubject:
    key: str
    label: str
    notification_type_ids: tuple[str, ...]


NOTIFICATION_TYPES: Final[tuple[NotificationType, ...]] = (
    NotificationType(
        "ticket_needs_approval",
        "Needs approval",
        "A Ticket has a proposal waiting for your decision.",
        True,
    ),
    NotificationType(
        "needs_input",
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
    NotificationType(
        "sprint_item_ping",
        "Sprint Item wants you",
        "A Sprint Item supervisor asked for you by name.",
        True,
    ),
)
NOTIFICATION_TYPE_BY_ID: Final[dict[str, NotificationType]] = {
    item.id: item for item in NOTIFICATION_TYPES
}

# What each subject offers. A Sprint Item supervisor offers two switches and no third:
# its turns complete about three thousand times a day, so a completed turn is not an
# event anybody can be told about. It says what it wants with a ping instead.
NOTIFICATION_SUBJECTS: Final[tuple[NotificationSubject, ...]] = (
    NotificationSubject(
        TICKET_NOTIFICATION_SUBJECT_KEY,
        "Tickets",
        (
            "ticket_needs_approval",
            "needs_input",
            "permission_requested",
            "worker_completed",
            "worker_failed",
        ),
    ),
    NotificationSubject(
        "chief_of_staff",
        "Chief of Staff",
        ("needs_input", "permission_requested", "worker_completed", "worker_failed"),
    ),
    NotificationSubject(
        SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY,
        "Sprint Item supervisors",
        ("sprint_item_ping", "worker_failed"),
    ),
)
NOTIFICATION_SUBJECT_BY_KEY: Final[dict[str, NotificationSubject]] = {
    item.key: item for item in NOTIFICATION_SUBJECTS
}


def notification_preference_is_valid(subject_key: str, notification_type: str) -> bool:
    subject = NOTIFICATION_SUBJECT_BY_KEY.get(subject_key)
    return subject is not None and notification_type in subject.notification_type_ids


@dataclass(frozen=True, slots=True)
class NotificationFact:
    fact_id: str
    notification_type: str
    subject_kind: NotificationSubjectKind
    subject_id: str
    subject_label: str
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
