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
def existing_floor_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        conversation_start_resolution, "FLOOR_DEFAULT_WORKSPACE_FOLDER", workspace
    )


class TestConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(
        self,
    ) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_conversation_system_under_test()


class TestInMemoryConversationSystemConformance(ConversationContractConformanceSuite):
    def open_system_under_test(
        self,
    ) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        return open_in_memory_conversation_system_under_test()


# The suite reads what reached the backend through ``backend_writes``, and the whole point
# of that reading is that it is sourced independently of the system's own record: a fate
# claiming the text is on the wire cannot be proved by the same code that returned the
# fate. The fake has no backend side for it to come from. Its ``backend_writes`` is a list
# the fake appends to on its way through, so asking it what the backend received is asking
# it to confirm its own bookkeeping, and the answer cannot come out any other way.
#
# Those exercises are the real system's, above, where the account is a child process's own
# and the question has an answer that can disagree. Here they are not run at all rather
# than run to a foregone conclusion.
#
# What the fake is still held to is everything four other seam tests lean on it for, and
# all of it survives: the fates, the queue positions, a held message's promotion, the
# permission-ask lifecycle, and a carried model change reaching the session.
BACKEND_WRITE_ATTESTED_EXERCISES = (
    # On a fake, the backend-truth exercises assert the fake's own arms back at it:
    # backend_writes, backend_cancellations and backend_model are counters it keeps itself,
    # so "the text is on the wire" and "the interrupt really stopped the agent" are true by
    # construction. The real system runs all 53; only this subject declines these.
    "test_send_now_while_busy_starts_and_really_kills_the_incumbent",
    "test_command_shaped_prose_at_the_front_stays_in_the_held_batch",
    "test_command_shaped_prose_later_in_the_line_stays_in_the_held_batch",
    "test_a_message_reaches_the_backend_whole_and_is_recorded_whole",
    "test_started_means_the_text_is_on_the_wire_when_send_returns",
    "test_injected_means_the_steered_text_is_on_the_wire_when_send_returns",
    "test_queued_text_reaches_no_backend_before_or_after_settling",
    "test_refused_text_never_reaches_the_backend_for_any_reason",
    "test_a_dequeued_delivery_records_its_own_fate_and_reaches_the_backend",
    "test_a_dequeued_delivery_that_fails_records_a_refusal_and_the_drain_continues",
    "test_shared_refusal_reasons_are_produced_only_by_their_own_cause",
    "test_backend_did_not_start_comes_only_from_a_backend_that_will_not_spawn",
    "test_session_did_not_load_comes_only_from_a_session_that_will_not_load",
    "test_confirmed_failed_steer_falls_back_to_queue",
    "test_a_refused_send_now_drains_the_queue_it_freed",
    "test_interrupt_while_running_really_stops_the_agent",
    "test_interrupt_sends_nothing_to_the_backend",
    "test_is_running_is_false_after_a_failure_and_after_an_interrupt",
    "test_the_sender_label_lands_on_every_prompt_fact",
    "test_turn_endings_are_recorded_facts_and_never_returned_fates",
    "test_a_send_carrying_a_model_change_changes_the_model_from_that_delivery_on",
    "test_a_send_without_a_change_leaves_model_and_effort_alone",
    "test_a_change_held_behind_a_busy_agent_lands_when_its_message_runs",
    "test_a_refused_delivery_carrying_a_change_changes_nothing",
    "test_a_steer_with_a_change_falls_back_to_queue",
)

for _exercise in BACKEND_WRITE_ATTESTED_EXERCISES:
    # A name that stopped matching a suite method would silently stop dropping anything,
    # so the shadowing is only ever allowed to hide something that is really there.
    assert hasattr(ConversationContractConformanceSuite, _exercise), _exercise
    # Shadowed with a non-callable, which is how a subclass declines an inherited test:
    # pytest collects functions, so the name is simply not a test on this subject.
    setattr(TestInMemoryConversationSystemConformance, _exercise, None)
del _exercise


@pytest.mark.parametrize("backend_key", tuple(ConversationBackendKey))
def test_supervisor_wake_message_uses_the_same_durable_prompt_boundary_for_every_backend(
    backend_key: ConversationBackendKey,
) -> None:
    async def exercise() -> None:
        async with open_conversation_system_under_test() as subject:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id=f"wake-{backend_key.value}",
                    backend_key=backend_key,
                    model="a-model",
                )
            )
            fate = await subject.system.send(
                f"wake-{backend_key.value}",
                text_message_content("Review the manager wake."),
                sender_label="Panels",
                mode=PromptDeliveryMode.queue,
                sender_message_id=f"supervisor_delivery_{backend_key.value}",
            )
            assert fate == PromptDeliveryStarted()
            prompts = tuple(
                fact
                for fact in await subject.recorded_facts(f"wake-{backend_key.value}")
                if fact.kind is RecordedFactKind.prompt_delivered
            )
            assert len(prompts) == 1
            assert prompts[0].text == "Review the manager wake."
            assert prompts[0].sender_label == "Panels"
            assert prompts[0].mode is PromptDeliveryMode.queue

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
