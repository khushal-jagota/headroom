"""The one fact-to-notification decision door."""

from __future__ import annotations

from planner.notifications.contracts import (
    NOTIFICATION_TYPE_BY_ID,
    NotificationFact,
    NotificationIntent,
)

_REASONS = {
    "ticket_needs_approval": "needs your approval",
    "ticket_needs_input": "needs your input",
    "permission_requested": "is waiting for permission",
    "worker_completed": "has a completed worker reply",
    "worker_failed": "has a failed worker reply",
}


def decide_notification(
    fact: NotificationFact, *, enabled: bool
) -> NotificationIntent | None:
    """Apply saved policy to one normalized fact.

    Sources never call delivery and delivery never interprets source facts. Every
    notification must pass through this function.
    """
    if fact.notification_type not in NOTIFICATION_TYPE_BY_ID:
        raise ValueError(f"unknown notification type: {fact.notification_type}")
    if not enabled:
        return None
    reason = _REASONS[fact.notification_type]
    return NotificationIntent(
        fact_id=fact.fact_id,
        title="Panels",
        body=f"{fact.ticket_title} {reason}.",
        route=f"/#/ticket/{fact.ticket_id}",
        # OS notification replacement is the final overlap coalescing boundary.
        tag=f"panels-ticket-{fact.ticket_id}",
    )
