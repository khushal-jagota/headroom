"""How a stall alarm reads and where it goes: a push to Khushal, and a Chief message.

The push uses the existing Web Push and APNs adapters with the subscriptions and devices
the watchdog read from the database. The Chief message is the same HTTP call as
`panels send-message --chief`, made as the owner, with a 10 s limit and best effort: a
stuck server must not hold up or stop the push, which is the channel that does not
need it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

import httpx

from planner.message_delivery.contracts import MessageDeliveryMode
from planner.notifications.apns import APNsAdapter
from planner.notifications.contracts import (
    STALL_ALARM_NOTIFICATION_TYPE,
    APNsDevice,
    EdgeKey,
    NotificationIntent,
    PendingAPNsDelivery,
    PushSubscription,
    WebPushIdentity,
)
from planner.notifications.web_push import WebPushAdapter
from planner.stalls.contracts import TrackedWaitKind

CHIEF_MESSAGE_TIMEOUT_SECONDS: Final = 10
CHIEF_MESSAGE_PREFIX: Final = "[Panels watchdog]"
STALL_ALARM_PUSH_TAG: Final = "panels-stall-alarm"
# The APNs adapter takes a whole delivery but reads only its device, intent and creation
# time. The watchdog's push has no attention edge, so its edge is this fixed placeholder.
_STALL_ALARM_EDGE: Final = EdgeKey("panels", "stall", STALL_ALARM_NOTIFICATION_TYPE, 1)

_LOG = logging.getLogger(__name__)

# What stopped moving, in words. The detail then says which item, command or write.
KIND_SENTENCES: Final[dict[TrackedWaitKind, str]] = {
    TrackedWaitKind.mac_items_not_moving: "Mac events are not reaching the VPS",
    TrackedWaitKind.mac_command_unfetched: "the Mac has not fetched a command",
    TrackedWaitKind.mac_stop_unproven: "a stop on the Mac is not confirmed",
    TrackedWaitKind.mac_replacement_pending: (
        "the restarted Mac has not finished sending this conversation's events"
    ),
    TrackedWaitKind.mac_turn_silent: "a Mac turn has gone silent",
    TrackedWaitKind.mac_lease_failing: "the Mac's leases are not accepted",
    TrackedWaitKind.mac_item_still_saving: "a Mac event is still saving",
    TrackedWaitKind.vps_save_retrying: "a conversation save keeps failing",
    TrackedWaitKind.mac_cancel_retirement: "retiring a Mac agent after a failed cancel is stuck",
}


@dataclass(frozen=True, slots=True)
class StallAlarm:
    """One thing to alarm on. alarm_key is stable for one stall episode: the watchdog
    alarms a key once, and again only after it has gone and come back."""

    alarm_key: str
    text: str
    ticket_id: str | None


@dataclass(frozen=True, slots=True)
class PushTargets:
    """What the watchdog read for the push. web_push_identity is None before the server
    has made one, which also means no browser can have subscribed."""

    web_push_identity: WebPushIdentity | None
    subscriptions: tuple[PushSubscription, ...]
    apns_devices: tuple[APNsDevice, ...]


def since_utc(at_ms: int) -> str:
    return datetime.fromtimestamp(at_ms / 1000, UTC).strftime("%H:%M")


def stall_text(
    *,
    kind: TrackedWaitKind,
    conversation_id: str | None,
    ticket_title: str | None,
    detail: str,
    stalled_since_ms: int,
) -> str:
    """Names the Ticket and the conversation, says what stopped and since when."""
    if ticket_title is not None:
        subject = f'Ticket "{ticket_title}" (conversation {conversation_id})'
    elif conversation_id is not None:
        subject = f"Conversation {conversation_id}"
    else:
        subject = "Panels"
    return f"{subject}: {KIND_SENTENCES[kind]}. {detail}, since {since_utc(stalled_since_ms)} UTC."


def push_intent(alarms: Sequence[StallAlarm]) -> NotificationIntent:
    """One push for everything new in a run, so one stuck Mac is one notification."""
    first = alarms[0]
    body = first.text
    if len(alarms) > 1:
        body += f" And {len(alarms) - 1} more."
    route = (
        f"/#/workspace/{first.ticket_id}"
        if len(alarms) == 1 and first.ticket_id is not None
        else "/#/"
    )
    return NotificationIntent(
        title="Panels: work stalled", body=body, route=route, tag=STALL_ALARM_PUSH_TAG
    )


def send_push(
    intent: NotificationIntent,
    targets: PushTargets,
    *,
    web_push: WebPushAdapter,
    apns: APNsAdapter | None,
    origin: str | None,
    now_s: int,
) -> int:
    """Push to every subscription and device. Returns how many accepted it."""
    delivered = 0
    if targets.web_push_identity is not None:
        for subscription in targets.subscriptions:
            result = web_push.send(
                subscription,
                intent,
                targets.web_push_identity,
                subject=origin or "https://localhost",
            )
            if result.delivered:
                delivered += 1
            else:
                _LOG.warning("stall alarm web push failed: %s", result.error)
    if apns is not None and origin is not None:
        for device in targets.apns_devices:
            native = apns.send(
                PendingAPNsDelivery(_STALL_ALARM_EDGE, device, intent, 0, now_s),
                server_url=origin,
                now=now_s,
            )
            if native.delivered:
                delivered += 1
            else:
                _LOG.warning("stall alarm native push failed: %s", native.error)
    return delivered


def message_chief(
    text: str, *, server_url: str, transport: httpx.BaseTransport | None = None
) -> bool:
    """Best effort. True only if the server accepted the message."""
    try:
        with httpx.Client(
            transport=transport, timeout=CHIEF_MESSAGE_TIMEOUT_SECONDS, trust_env=False
        ) as client:
            response = client.post(
                server_url.rstrip("/") + "/api/messages/send",
                json={
                    "target": {"kind": "chief", "id": "chief"},
                    "message": f"{CHIEF_MESSAGE_PREFIX} {text}",
                    "mode": MessageDeliveryMode.steer.value,
                },
            )
        body = response.json() if response.is_success else None
    except Exception as error:
        _LOG.warning("stall alarm could not reach the Chief: %r", error)
        return False
    if not isinstance(body, dict) or "error" in body or body.get("fate") == "refused":
        _LOG.warning("stall alarm Chief message not accepted: HTTP %s", response.status_code)
        return False
    return True
