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

from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.events import UserInputAnswer, UserInputOption, UserInputQuestion
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
    TurnCannotEndWhilePermissionAskIsPending,
    TurnCannotEndWhileUserInputIsPending,
)
from planner.conversation.message_content import text_message_content


async def _system_with_a_pending_ask() -> tuple[InMemoryConversationSystem, str]:
    system = InMemoryConversationSystem()
    await system.start_conversation(
        ConversationStartRequest(
            conversation_id="c",
            model="a-model",
            backend_key=ConversationBackendKey.hermes,
        )
    )
    await system.send("c", text_message_content("incumbent"), sender_label="owner")
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


def test_in_memory_user_input_waits_for_the_complete_answer_map() -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await system.start_conversation(
            ConversationStartRequest(
                conversation_id="c",
                model="a-model",
                backend_key=ConversationBackendKey.claude,
            )
        )
        await system.send("c", text_message_content("incumbent"), sender_label="owner")
        request_id = system.raise_user_input(
            "c",
            (
                UserInputQuestion(
                    question_id="q1",
                    header="Choice",
                    question="Which?",
                    options=(UserInputOption(label="One", description="first"),),
                    multi_select=False,
                    allow_other=True,
                ),
            ),
        )
        assert await system.has_pending_user_input("c") is True
        with pytest.raises(TurnCannotEndWhileUserInputIsPending):
            system.complete_running_turn("c")
        assert system.answer_user_input(
            "c", request_id, (UserInputAnswer(question_id="q1", answers=("typed",)),)
        )
        assert system.backend_user_input_answers("c", request_id) == (
            UserInputAnswer(question_id="q1", answers=("typed",)),
        )
        system.complete_running_turn("c")

    asyncio.run(exercise())
