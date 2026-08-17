"""The one fact-to-notification decision door."""

from __future__ import annotations

from planner.notifications.contracts import (
    NOTIFICATION_TYPE_BY_ID,
    NotificationFact,
    NotificationIntent,
)

_REASONS = {
    "ticket_needs_approval": "needs your approval",
    "needs_input": "needs your input",
    "permission_requested": "is waiting for permission",
    "worker_completed": "has a completed worker reply",
    "worker_failed": "has a failed worker reply",
}


def _subject_route(fact: NotificationFact) -> str:
    if fact.subject_kind == "ticket":
        return f"/#/workspace/{fact.subject_id}"
    if fact.subject_kind == "agent":
        return f"/#/agents/{fact.subject_id.replace('_', '-')}"
    raise ValueError(f"unknown notification subject kind: {fact.subject_kind}")


def decide_notification(fact: NotificationFact, *, enabled: bool) -> NotificationIntent | None:
    """Apply saved policy to one normalized fact.

    Sources never call delivery and delivery never interprets source facts. Every
    notification must pass through this function.
    """
    if fact.notification_type not in NOTIFICATION_TYPE_BY_ID:
        raise ValueError(f"unknown notification type: {fact.notification_type}")
    if fact.subject_kind not in {"ticket", "agent"}:
        raise ValueError(f"unknown notification subject kind: {fact.subject_kind}")
    if not enabled:
        return None
    reason = _REASONS[fact.notification_type]
    return NotificationIntent(
        fact_id=fact.fact_id,
        title="Panels",
        body=f"{fact.subject_label} {reason}.",
        route=_subject_route(fact),
        # OS notification replacement is the final overlap coalescing boundary.
        tag=f"panels-{fact.subject_kind}-{fact.subject_id}",
    )
