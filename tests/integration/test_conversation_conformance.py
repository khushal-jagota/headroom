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

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.logic import conversation_start_resolution
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore


@pytest.fixture(autouse=True)
def existing_floor_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(conversation_start_resolution, "FLOOR_DEFAULT_WORKSPACE_FOLDER", workspace)






class TestConversationSystemConformance:
    """Only admitted wire guards, using their unchanged shared definitions."""

    _run = ConversationContractConformanceSuite._run

    test_send_now_runs_next_and_held_prompts_keep_their_order_behind_it = (
        ConversationContractConformanceSuite.test_send_now_runs_next_and_held_prompts_keep_their_order_behind_it
    )

    test_command_shaped_prose_at_the_front_stays_in_the_held_batch = (
        ConversationContractConformanceSuite.test_command_shaped_prose_at_the_front_stays_in_the_held_batch
    )

    test_command_shaped_prose_later_in_the_line_stays_in_the_held_batch = (
        ConversationContractConformanceSuite.test_command_shaped_prose_later_in_the_line_stays_in_the_held_batch
    )

    test_a_message_reaches_the_backend_whole_and_is_recorded_whole = (
        ConversationContractConformanceSuite.test_a_message_reaches_the_backend_whole_and_is_recorded_whole
    )

    test_started_means_the_text_is_on_the_wire_when_send_returns = (
        ConversationContractConformanceSuite.test_started_means_the_text_is_on_the_wire_when_send_returns
    )

    test_injected_means_the_steered_text_is_on_the_wire_when_send_returns = (
        ConversationContractConformanceSuite.test_injected_means_the_steered_text_is_on_the_wire_when_send_returns
    )

    test_queued_text_reaches_no_backend_before_or_after_settling = (
        ConversationContractConformanceSuite.test_queued_text_reaches_no_backend_before_or_after_settling
    )

    test_refused_text_never_reaches_the_backend_for_any_reason = (
        ConversationContractConformanceSuite.test_refused_text_never_reaches_the_backend_for_any_reason
    )

    test_a_dequeued_delivery_records_its_own_fate_and_reaches_the_backend = (
        ConversationContractConformanceSuite.test_a_dequeued_delivery_records_its_own_fate_and_reaches_the_backend
    )

    test_a_dequeued_delivery_that_fails_records_a_refusal_and_the_drain_continues = (
        ConversationContractConformanceSuite.test_a_dequeued_delivery_that_fails_records_a_refusal_and_the_drain_continues
    )

    test_confirmed_failed_steer_falls_back_to_queue = (
        ConversationContractConformanceSuite.test_confirmed_failed_steer_falls_back_to_queue
    )

    test_a_refused_send_now_drains_the_queue_it_freed = (
        ConversationContractConformanceSuite.test_a_refused_send_now_drains_the_queue_it_freed
    )

    test_interrupt_sends_nothing_to_the_backend = (
        ConversationContractConformanceSuite.test_interrupt_sends_nothing_to_the_backend
    )

    test_nothing_ever_answers_a_permission_ask_by_itself = (
        ConversationContractConformanceSuite.test_nothing_ever_answers_a_permission_ask_by_itself
    )

    test_a_pending_ask_holds_the_turn_open_until_it_is_answered = (
        ConversationContractConformanceSuite.test_a_pending_ask_holds_the_turn_open_until_it_is_answered
    )

    test_an_answer_lands_only_on_a_pending_ask_of_the_live_turn = (
        ConversationContractConformanceSuite.test_an_answer_lands_only_on_a_pending_ask_of_the_live_turn
    )

    test_a_send_carrying_a_model_change_changes_the_model_from_that_delivery_on = (
        ConversationContractConformanceSuite.test_a_send_carrying_a_model_change_changes_the_model_from_that_delivery_on
    )

    test_a_send_without_a_change_leaves_model_and_effort_alone = (
        ConversationContractConformanceSuite.test_a_send_without_a_change_leaves_model_and_effort_alone
    )

    test_a_change_held_behind_a_busy_agent_lands_when_its_message_runs = (
        ConversationContractConformanceSuite.test_a_change_held_behind_a_busy_agent_lands_when_its_message_runs
    )

    test_a_refused_delivery_carrying_a_change_changes_nothing = (
        ConversationContractConformanceSuite.test_a_refused_delivery_carrying_a_change_changes_nothing
    )

    test_kill_stops_the_turn_and_discards_every_held_message = (
        ConversationContractConformanceSuite.test_kill_stops_the_turn_and_discards_every_held_message
    )

    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_conversation_system_under_test()


def test_supervisor_wake_message_crosses_the_hermes_adapter_prompt_boundary() -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="wake-hermes",
                    backend_key=ConversationBackendKey.hermes,
                    model="a-model",
                )
            )
            fate = await subject.system.send(
                "wake-hermes",
                text_message_content("Review the manager wake."),
                sender_label="Panels",
                mode=PromptDeliveryMode.queue,
                sender_message_id="supervisor_delivery_hermes",
            )
            assert fate == PromptDeliveryStarted()
            prompts = tuple(
                fact
                for fact in await subject.recorded_facts("wake-hermes")
                if fact.kind is RecordedFactKind.prompt_delivered
            )
            assert len(prompts) == 1
            assert prompts[0].text == "Review the manager wake."
            assert prompts[0].sender_label == "Panels"
            assert prompts[0].mode is PromptDeliveryMode.queue

    asyncio.run(exercise())


def test_busy_manager_holds_a_wake_without_interrupt_then_delivers_it() -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="busy-manager",
                    backend_key=ConversationBackendKey.hermes,
                    model="a-model",
                )
            )
            assert (
                await subject.system.send(
                    "busy-manager",
                    text_message_content("incumbent work"),
                    sender_label="owner",
                )
                == PromptDeliveryStarted()
            )
            queued = await subject.system.send(
                "busy-manager",
                text_message_content("Review the manager wake."),
                sender_label="Panels",
                mode=PromptDeliveryMode.queue,
                sender_message_id="supervisor_delivery_wake_busy",
            )
            assert queued == PromptDeliveryQueued(queue_position=1)
            assert await subject.backend_cancellations("busy-manager") == 0
            before = await subject.backend_writes("busy-manager")
            assert len(before) == 1
            assert before[0].text.endswith("incumbent work")

            await subject.complete_running_turn("busy-manager")

            assert await subject.backend_cancellations("busy-manager") == 0
            writes = await subject.backend_writes("busy-manager")
            assert len(writes) == 2
            assert writes[0].text.endswith("incumbent work")
            assert writes[1].text.endswith("Review the manager wake.")
            assert writes[1].sender_label == "Panels"

    asyncio.run(exercise())


def test_marker_failure_preserves_the_wake_until_explicit_storage_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = ConversationStore.mark_held_sender_messages_leaving_queue
    calls = 0
    failed = asyncio.Event()

    async def fail_once(
        store: ConversationStore,
        conversation_id: str,
        sender_message_ids: tuple[str, ...],
    ) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            failed.set()
            raise OSError("marker unavailable")
        return await original(store, conversation_id, sender_message_ids)

    monkeypatch.setattr(ConversationStore, "mark_held_sender_messages_leaving_queue", fail_once)

    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="marker-retry",
                    backend_key=ConversationBackendKey.hermes,
                    model="a-model",
                )
            )
            await subject.system.send(
                "marker-retry",
                text_message_content("incumbent work"),
                sender_label="owner",
            )
            assert await subject.system.send(
                "marker-retry",
                text_message_content("Review the manager wake."),
                sender_label="Panels",
                mode=PromptDeliveryMode.queue,
                sender_message_id="supervisor_delivery_wake_retry",
            ) == PromptDeliveryQueued(queue_position=1)

            completing = asyncio.create_task(subject.complete_running_turn("marker-retry"))
            await failed.wait()
            assert not completing.done()
            assert subject._store.resume_recording("marker-retry") == 1
            await completing

            writes = await subject.backend_writes("marker-retry")
            assert len(writes) == 2
            assert writes[0].text.endswith("incumbent work")
            assert writes[1].text.endswith("Review the manager wake.")
            assert calls == 2

    asyncio.run(exercise())


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
            assert (
                await subject.system.send(
                    "c", text_message_content("incumbent"), sender_label="owner"
                )
                == PromptDeliveryStarted()
            )
            await subject.arm_backend_connection_loss("c")

            assert (
                await subject.system.send(
                    "c",
                    text_message_content("uncertain steer"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.steer,
                )
                == PromptDeliveryUncertain()
            )
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
