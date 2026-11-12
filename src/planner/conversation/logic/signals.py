"""The two independent marks owned by one conversation."""

from planner.conversation.contracts import ConversationSignal


def conversation_signal(
    *,
    turn_is_running: bool,
    has_live_background_task: bool,
    unread_message_to_owner: bool,
) -> ConversationSignal:
    """Work and an explicit unread message stay separate, including while both exist.

    An agent is working while a turn runs and while a background task it started is
    still running after its turn ended.
    """
    return {
        "working": turn_is_running or has_live_background_task,
        "message_for_owner": unread_message_to_owner,
    }
