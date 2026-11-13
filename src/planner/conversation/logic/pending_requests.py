"""Unanswered requests live only until their conversation turn ends."""

from planner.conversation.events import ConversationEventKind

REQUEST_TERMINATING_EVENT_KIND = ConversationEventKind.turn_ended


def advance_pending_requests(
    permission_ids: frozenset[str],
    input_ids: frozenset[str],
    kind: str,
    *,
    ask_id: str | None = None,
    request_id: str | None = None,
) -> tuple[frozenset[str], frozenset[str]]:
    """Apply one event without treating a prompt or steer as a turn boundary."""
    if kind == REQUEST_TERMINATING_EVENT_KIND:
        return frozenset(), frozenset()
    if kind == ConversationEventKind.permission_asked and ask_id is not None:
        permission_ids = permission_ids | {ask_id}
    elif kind == ConversationEventKind.permission_answered and ask_id is not None:
        permission_ids = permission_ids - {ask_id}
    elif kind == ConversationEventKind.user_input_requested and request_id is not None:
        input_ids = input_ids | {request_id}
    elif kind in (
        ConversationEventKind.user_input_answered,
        ConversationEventKind.user_input_failed,
    ) and request_id is not None:
        input_ids = input_ids - {request_id}
    return permission_ids, input_ids
