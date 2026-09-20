"""Contracts shared by notification sources, policy, APIs, and delivery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from planner.core.contracts import Principal, PrincipalKind

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
    principal_kind: PrincipalKind
    notification_type_ids: tuple[str, ...]


NOTIFICATION_TYPES: Final[tuple[NotificationType, ...]] = (
    NotificationType(
        "awaiting_reply",
        "Message",
        "An unread message needs your reply.",
        True,
    ),
    NotificationType(
        "awaiting_approval",
        "Needs approval",
        "A Ticket has a proposal waiting for your decision.",
        True,
    ),
    NotificationType(
        "assigned",
        "Assigned",
        "A Ticket stage is assigned to you.",
        True,
    ),
    NotificationType(
        "errored",
        "Error",
        "A worker turn or Ticket failed and needs attention.",
        True,
    ),
)
NOTIFICATION_TYPE_BY_ID: Final[dict[str, NotificationType]] = {
    item.id: item for item in NOTIFICATION_TYPES
}

# What each subject offers. A Sprint Item conversation offers one switch: a failed
# turn. Nothing it does reaches the user by itself, and the user is present for the
# turns it takes, because a message of theirs is the only thing that starts one.
NOTIFICATION_SUBJECTS: Final[tuple[NotificationSubject, ...]] = (
    NotificationSubject(
        TICKET_NOTIFICATION_SUBJECT_KEY,
        "Tickets",
        PrincipalKind.ticket,
        (
            "awaiting_reply",
            "awaiting_approval",
            "assigned",
            "errored",
        ),
    ),
    NotificationSubject(
        "chief_of_staff",
        "Chief of Staff",
        PrincipalKind.chief,
        ("awaiting_reply", "errored"),
    ),
    NotificationSubject(
        SPRINT_ITEM_SUPERVISOR_NOTIFICATION_SUBJECT_KEY,
        "Sprint Item supervisors",
        PrincipalKind.sprint_item,
        ("awaiting_reply", "errored"),
    ),
)
NOTIFICATION_SUBJECT_BY_KEY: Final[dict[str, NotificationSubject]] = {
    item.key: item for item in NOTIFICATION_SUBJECTS
}


def notification_preference_is_valid(subject_key: str, notification_type: str) -> bool:
    subject = NOTIFICATION_SUBJECT_BY_KEY.get(subject_key)
    return subject is not None and notification_type in subject.notification_type_ids


@dataclass(frozen=True, slots=True)
class EdgeKey:
    """What one false-to-true attention transition is called, everywhere."""

    subject_kind: str
    subject_id: str
    notification_type: str
    generation: int


@dataclass(frozen=True, slots=True)
class AttentionEdge:
    key: EdgeKey
    subject: Principal
    subject_label: str
    occurred_at: int

    @property
    def notification_type(self) -> str:
        return self.key.notification_type


@dataclass(frozen=True, slots=True)
class NotificationIntent:
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
    edge: EdgeKey
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
