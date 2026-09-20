"""The one edge-to-notification decision door."""

from __future__ import annotations

from planner.core.contracts import PrincipalKind
from planner.notifications.contracts import (
    NOTIFICATION_TYPE_BY_ID,
    AttentionEdge,
    NotificationIntent,
)

_REASONS = {
    "awaiting_reply": "has a message for you",
    "awaiting_approval": "needs your approval",
    "assigned": "is assigned to you",
    "errored": "has an error",
}


def _subject_route(edge: AttentionEdge) -> str:
    if edge.subject.kind is PrincipalKind.ticket:
        return f"/#/workspace/{edge.subject.id}"
    if edge.subject.kind is PrincipalKind.chief:
        return "/#/agents/chief-of-staff"
    if edge.subject.kind is PrincipalKind.sprint_item:
        return f"/#/workspace/item/{edge.subject.id}"
    raise ValueError(f"unknown notification subject kind: {edge.subject.kind.value}")


def _subject_tag(edge: AttentionEdge) -> str:
    """Keep the existing OS replacement key while policy uses Principals."""
    if edge.subject.kind is PrincipalKind.chief:
        return "panels-agent-chief_of_staff"
    return f"panels-{edge.subject.kind.value}-{edge.subject.id}"


def decide_notification(edge: AttentionEdge, *, enabled: bool) -> NotificationIntent | None:
    """Apply saved policy to one attention edge.

    Sources never call delivery and delivery never interprets source state. Every
    notification must pass through this function.
    """
    if edge.notification_type not in NOTIFICATION_TYPE_BY_ID:
        raise ValueError(f"unknown notification type: {edge.notification_type}")
    if edge.subject.kind not in {
        PrincipalKind.ticket,
        PrincipalKind.chief,
        PrincipalKind.sprint_item,
    }:
        raise ValueError(f"unknown notification subject kind: {edge.subject.kind.value}")
    if not enabled:
        return None
    reason = _REASONS[edge.notification_type]
    return NotificationIntent(
        title="Panels",
        body=f"{edge.subject_label} {reason}.",
        route=_subject_route(edge),
        # OS notification replacement is the final overlap coalescing boundary.
        tag=_subject_tag(edge),
    )
