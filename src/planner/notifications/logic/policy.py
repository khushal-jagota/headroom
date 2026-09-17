"""The one fact-to-notification decision door."""

from __future__ import annotations

from planner.core.contracts import PrincipalKind
from planner.notifications.contracts import (
    NOTIFICATION_TYPE_BY_ID,
    NotificationFact,
    NotificationIntent,
)

_REASONS = {
    "awaiting_reply": "has a message for you",
    "awaiting_approval": "needs your approval",
    "assigned": "is assigned to you",
    "errored": "has an error",
}


def _subject_route(fact: NotificationFact) -> str:
    if fact.subject.kind is PrincipalKind.ticket:
        return f"/#/workspace/{fact.subject.id}"
    if fact.subject.kind is PrincipalKind.chief:
        return "/#/agents/chief-of-staff"
    if fact.subject.kind is PrincipalKind.sprint_item:
        return f"/#/workspace/item/{fact.subject.id}"
    raise ValueError(f"unknown notification subject kind: {fact.subject.kind.value}")


def _subject_tag(fact: NotificationFact) -> str:
    """Keep the existing OS replacement key while policy uses Principals."""
    if fact.subject.kind is PrincipalKind.chief:
        return "panels-agent-chief_of_staff"
    return f"panels-{fact.subject.kind.value}-{fact.subject.id}"


def decide_notification(fact: NotificationFact, *, enabled: bool) -> NotificationIntent | None:
    """Apply saved policy to one normalized fact.

    Sources never call delivery and delivery never interprets source facts. Every
    notification must pass through this function.
    """
    if fact.notification_type not in NOTIFICATION_TYPE_BY_ID:
        raise ValueError(f"unknown notification type: {fact.notification_type}")
    if fact.subject.kind not in {
        PrincipalKind.ticket,
        PrincipalKind.chief,
        PrincipalKind.sprint_item,
    }:
        raise ValueError(f"unknown notification subject kind: {fact.subject.kind.value}")
    if not enabled:
        return None
    reason = _REASONS[fact.notification_type]
    return NotificationIntent(
        fact_id=fact.fact_id,
        title="Panels",
        body=f"{fact.subject_label} {reason}.",
        route=_subject_route(fact),
        # OS notification replacement is the final overlap coalescing boundary.
        tag=_subject_tag(fact),
    )
