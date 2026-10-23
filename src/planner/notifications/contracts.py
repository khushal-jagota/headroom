"""Contracts shared by notification sources, policy, APIs, and delivery."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Literal

from planner.core.contracts import Principal, PrincipalKind

TICKET_NOTIFICATION_SUBJECT_KEY: Final = "tickets"
MANAGER_NOTIFICATION_SUBJECT_KEY: Final = "managers"


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
        "awaiting_answer",
        "Needs an answer",
        "A worker is waiting on an answer only you can give.",
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

# What each subject offers. A Manager conversation offers one switch: a failed
# turn. Nothing it does reaches the user by itself, and the user is present for the
# turns it takes, because a message of theirs is the only thing that starts one.
NOTIFICATION_SUBJECTS: Final[tuple[NotificationSubject, ...]] = (
    NotificationSubject(
        TICKET_NOTIFICATION_SUBJECT_KEY,
        "Tickets",
        PrincipalKind.ticket,
        (
            "awaiting_reply",
            "awaiting_answer",
            "awaiting_approval",
            "assigned",
            "errored",
        ),
    ),
    NotificationSubject(
        "chief_of_staff",
        "Chief of Staff",
        PrincipalKind.chief,
        ("awaiting_reply", "awaiting_answer", "errored"),
    ),
    NotificationSubject(
        MANAGER_NOTIFICATION_SUBJECT_KEY,
        "Managers",
        PrincipalKind.manager,
        ("awaiting_reply", "awaiting_answer", "errored"),
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


@dataclass(frozen=True, slots=True)
class APNsConfig:
    key_path: str = field(repr=False)
    key_id: str
    team_id: str
    bundle_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class APNsDevice:
    device_id: str
    device_token: str = field(repr=False)
    bundle_id: str
    environment: Literal["development", "production"]
    registration_version: int
    updated_at: int


@dataclass(frozen=True, slots=True)
class PendingAPNsDelivery:
    edge: EdgeKey
    device: APNsDevice
    intent: NotificationIntent
    attempts: int
    created_at: int


@dataclass(frozen=True, slots=True)
class APNsResult:
    delivered: bool
    dead_token: bool = False
    retryable: bool = False
    error: str | None = None
    invalidated_at_ms: int | None = None
