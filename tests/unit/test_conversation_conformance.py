"""The whole conversation contract, against the real system over real ACP child processes."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path

import pytest
from tests.support.conversation_contract_conformance import (
    ConversationContractConformanceSuite,
    ConversationSystemUnderTest,
    RecordedFactKind,
    RecordedTurnEnding,
)
from tests.support.conversation_system_under_test import (
    open_conversation_system_under_test,
)
from tests.support.in_memory_conversation_system_under_test import (
    open_in_memory_conversation_system_under_test,
)

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.logic import conversation_start_resolution
from planner.conversation.message_content import text_message_content


@pytest.fixture(autouse=True)
def existing_floor_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        conversation_start_resolution, "FLOOR_DEFAULT_WORKSPACE_FOLDER", workspace
    )


class TestConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_conversation_system_under_test()


class TestInMemoryConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_in_memory_conversation_system_under_test()


def test_a_private_steer_lost_with_its_connection_is_uncertain_and_stoppable() -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c",
                    model="a-model",
                    backend_key=ConversationBackendKey.hermes,
                )
            )
            assert await subject.system.send(
                "c", text_message_content("incumbent"), sender_label="owner"
            ) == PromptDeliveryStarted()
            await subject.arm_backend_connection_loss("c")

            assert await subject.system.send(
                "c",
                text_message_content("uncertain steer"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            ) == PromptDeliveryUncertain()
            account = await subject.agent_account("c")
            assert account["steer_attempts"] == []
            assert account["steer_writes"] == []
            uncertain = tuple(
                fact
                for fact in await subject.recorded_facts("c")
                if fact.kind is RecordedFactKind.prompt_delivery_uncertain
            )
            assert tuple(fact.text for fact in uncertain) == ("uncertain steer",)

            assert await subject.system.is_running("c") is True
            await subject.system.interrupt("c")
            assert await subject.system.is_running("c") is False
            endings = tuple(
                fact.turn_ending
                for fact in await subject.recorded_facts("c")
                if fact.kind is RecordedFactKind.turn_ended
            )
            assert endings == (RecordedTurnEnding.interrupted,)

    asyncio.run(asyncio.wait_for(exercise(), 30.0))
