"""The two independent marks owned by one conversation."""

from planner.conversation.contracts import ConversationSignal


def conversation_signal(*, is_running: bool, unread_message_to_owner: bool) -> ConversationSignal:
    """Work and an explicit unread message stay separate, including while both exist."""
    return {"working": is_running, "message_for_owner": unread_message_to_owner}
