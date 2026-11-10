"""Read one shared signal projection for each exact conversation."""

from __future__ import annotations

from collections.abc import Iterable

from planner.conversation.contracts import ConversationAttention, ConversationSystem
from planner.conversation.logic.signals import conversation_signal
from planner.conversation.storage import ConversationAttentionFacts, ConversationStore


async def read_conversation_signals(
    conversation_system: ConversationSystem,
    conversation_record: ConversationStore,
    conversation_ids: Iterable[str],
) -> dict[str, ConversationAttention]:
    """Read runtime work and durable explicit-message facts without any Ticket state."""
    ids = set(conversation_ids)
    durable = await conversation_record.attention_facts(ids)

    # Each check reads live state and never waits, so they run in turn. Gathering them
    # cost three tasks per conversation, thousands for one list read.
    signals: dict[str, ConversationAttention] = {}
    for conversation_id in ids:
        running = await conversation_system.is_running(conversation_id)
        permission = await conversation_system.has_pending_permission_ask(conversation_id)
        question = await conversation_system.has_pending_user_input(conversation_id)
        fact = durable.get(conversation_id, ConversationAttentionFacts(False, False, False))
        signals[conversation_id] = ConversationAttention(
            signal=conversation_signal(
                turn_is_running=running,
                has_live_background_task=fact.has_live_background_task,
                unread_message_to_owner=fact.unread_message_to_owner,
            ),
            awaiting_answer=permission or question,
            last_turn_failed=fact.last_turn_failed,
        )
    return signals
