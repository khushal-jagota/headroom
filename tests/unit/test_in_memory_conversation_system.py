"""Behaviour that belongs to the in-memory conversation system alone.

The contract says an agent's permission ask always waits, and the fake models a waiting
agent by refusing to let its turn finish while an ask is unanswered. That is the fake's
own modelling choice, not something every conversation system can be held to — a real
agent decides for itself when it sends its prompt response — so it is tested here rather
than in the conformance suite.
"""

from __future__ import annotations

import asyncio

import pytest

from planner.conversation2.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation2.in_memory_conversation_system import (
    InMemoryConversationSystem,
    TurnCannotEndWhilePermissionAskIsPending,
)


async def _system_with_a_pending_ask() -> tuple[InMemoryConversationSystem, str]:
    system = InMemoryConversationSystem()
    await system.start_conversation(
        ConversationStartRequest(conversation_id="c", backend_key=ConversationBackendKey.hermes)
    )
    await system.send("c", "incumbent", sender_label="owner")
    return system, system.raise_permission_ask("c")


def test_completing_a_turn_with_a_pending_ask_raises() -> None:
    async def exercise() -> None:
        system, _ = await _system_with_a_pending_ask()
        with pytest.raises(TurnCannotEndWhilePermissionAskIsPending):
            system.complete_running_turn("c")
        assert await system.is_running("c") is True

    asyncio.run(exercise())


def test_completing_a_turn_is_allowed_once_the_ask_is_answered() -> None:
    async def exercise() -> None:
        system, ask_id = await _system_with_a_pending_ask()
        assert system.answer_permission_ask("c", ask_id, "allow-once") is True
        system.complete_running_turn("c")
        assert await system.is_running("c") is False

    asyncio.run(exercise())
