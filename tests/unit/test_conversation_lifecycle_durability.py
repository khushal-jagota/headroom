"""Durable knowledge, atomic receipts, and recovery without restored capabilities."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support.conversation_test_loop import conversation_test_loop
from tests.unit.test_conversation_system import (
    _FakeBackendChild,
    _Harness,
    _run,
    _start,
)

from planner.conversation.contracts import AnswerFate, PromptDeliveryMode
from planner.conversation.events import (
    PermissionAnsweredEventPayload,
    PermissionAskedEventPayload,
    PromptEventPayload,
    UserInputAnswer,
    UserInputAnsweredEventPayload,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
    LifecycleTransition,
)
from planner.conversation.lifecycle_storage import ConversationRuntimeAlreadyOwned
from planner.conversation.message_content import text_message_content
from planner.conversation.storage import ConversationStore
from planner.core.db import connect, create_schema


@pytest.fixture
def durable(tmp_path: Path) -> Iterator[_Harness]:
    path = tmp_path / "isolated.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    harness = _Harness(path)
    with conversation_test_loop() as runner:
        try:
            yield harness
        finally:
            runner.run(harness.system.shutdown())


async def _paused(store: ConversationStore) -> None:
    async def wait() -> None:
        while not store.recording_failures("c"):
            await asyncio.sleep(0.001)

    await asyncio.wait_for(wait(), 5)


def test_intent_failure_prevents_provider_effect_and_rolls_back(durable: _Harness) -> None:
    async def exercise() -> None:
        await _start(durable, "c")

        def fail(conn: sqlite3.Connection, candidate: LifecycleTransition) -> None:
            assert conn.in_transaction
            assert candidate.contract_version == 1
            raise RuntimeError("intent rejected")

        durable.store._transaction_participant = fail
        with pytest.raises(RuntimeError, match="intent rejected"):
            await durable.system.send("c", text_message_content("secret"), sender_label="owner")
        assert await durable.store.lifecycle_facts("c") == ()
        assert durable.spawned_conversation_ids == []
        assert await durable.events("c") == ()

    _run(exercise)




def test_receipt_and_candidate_participant_failure_roll_back_together(durable: _Harness) -> None:
    async def exercise() -> None:
        await _start(durable, "c")
        turn = await durable.store.reserve_turn("c")
        conn = connect(durable.store._db_path)
        conn.execute("CREATE TABLE participant_capture (value TEXT)")
        conn.close()

        def fail(conn: sqlite3.Connection, candidate: LifecycleTransition) -> None:
            assert candidate.receipt_sequences == (1,)
            assert any(f.status is LifecycleStatus.started for f in candidate.facts)
            conn.execute("INSERT INTO participant_capture VALUES ('candidate')")
            raise RuntimeError("capture failed")

        durable.store._transaction_participant = fail
        with pytest.raises(RuntimeError, match="capture failed"):
            await durable.store.append_delivered_prompt(
                "c",
                prompt=PromptEventPayload(
                    content=text_message_content("work"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.queue,
                ),
                model_change=None,
                lifecycle=(
                    LifecycleFact(
                        LifecycleKind.turn,
                        str(turn),
                        turn,
                        LifecycleOperation.turn,
                        LifecycleStatus.running,
                    ),
                    LifecycleFact(
                        LifecycleKind.attempt,
                        f"prompt:{turn}",
                        turn,
                        LifecycleOperation.prompt,
                        LifecycleStatus.started,
                    ),
                ),
            )
        assert await durable.events("c") == ()
        assert {f.status for f in await durable.store.lifecycle_facts("c")} == {
            LifecycleStatus.intent
        }
        conn = connect(durable.store._db_path)
        assert conn.execute("SELECT COUNT(*) FROM participant_capture").fetchone()[0] == 0
        conn.close()

    _run(exercise)




def test_crash_gap_is_unknown_and_never_resends(durable: _Harness) -> None:
    async def exercise() -> None:
        await _start(durable, "c")

        def fail(conn: sqlite3.Connection, candidate: LifecycleTransition) -> None:
            if candidate.receipt_sequences:
                raise RuntimeError("receipt disk failure")

        durable.store._transaction_participant = fail
        sending = asyncio.create_task(
            durable.system.send("c", text_message_content("once"), sender_label="owner")
        )
        await _paused(durable.store)
        assert durable.backend("c").written_texts() == ("once",)
        assert {f.status for f in await durable.store.lifecycle_facts("c")} == {
            LifecycleStatus.intent
        }
        assert await durable.events("c") == ()
        await durable.system.shutdown()
        with pytest.raises(asyncio.CancelledError):
            await sending
        restarted = _Harness(Path(durable.store._db_path))
        try:
            facts = await restarted.store.lifecycle_facts("c")
            assert {f.status for f in facts} == {
                LifecycleStatus.unknown,
                LifecycleStatus.unavailable,
            }
            assert not await restarted.system.is_running("c")
            assert await restarted.events("c") == ()
            assert restarted.spawned_conversation_ids == []
            await restarted.system.send("c", text_message_content("new"), sender_label="owner")
            token = restarted.backend("c").live_turn_token
            assert token is not None and token.turn_number == 2
            assert restarted.backend("c").written_texts() == ("new",)
        finally:
            await restarted.system.shutdown()

    _run(exercise)


@pytest.mark.parametrize("request_kind", ["permission", "user_input"])
def test_answer_commit_failure_and_caller_cancellation_never_repeat_callback(
    durable: _Harness, monkeypatch: pytest.MonkeyPatch, request_kind: str
) -> None:
    counts = 0
    original_permission = _FakeBackendChild.answer_permission_ask
    original_input = _FakeBackendChild.answer_user_input

    async def permission(
        self: _FakeBackendChild, ask_id: str, option_id: str, **kwargs: object
    ) -> None:
        nonlocal counts
        counts += 1
        return await original_permission(self, ask_id, option_id)

    async def user_input(
        self: _FakeBackendChild,
        request_id: str,
        answers: tuple[UserInputAnswer, ...],
        **kwargs: object,
    ) -> None:
        nonlocal counts
        counts += 1
        return await original_input(self, request_id, answers)

    monkeypatch.setattr(_FakeBackendChild, "answer_permission_ask", permission)
    monkeypatch.setattr(_FakeBackendChild, "answer_user_input", user_input)

    async def exercise() -> None:
        await _start(durable, "c")
        await durable.system.send("c", text_message_content("work"), sender_label="owner")
        if request_kind == "permission":
            request_id = await durable.raise_permission_ask("c")

            async def answer() -> AnswerFate:
                return await durable.system.answer_permission_ask("c", request_id, "deny")
        else:
            request = await durable.request_user_input("c")
            request_id = request.request_id
            answers = (
                UserInputAnswer(question_id="scope", answers=("Backend",)),
                UserInputAnswer(question_id="timing", answers=("Now",)),
            )

            async def answer() -> AnswerFate:
                return await durable.system.answer_user_input("c", request_id, answers)

        fail = True

        def participant(conn: sqlite3.Connection, candidate: LifecycleTransition) -> None:
            if fail and any(
                f.operation.endswith("_answer") and f.status is LifecycleStatus.accepted
                for f in candidate.facts
            ):
                raise RuntimeError("answer settlement failed")

        durable.store._transaction_participant = participant
        answering = asyncio.create_task(answer())
        await _paused(durable.store)
        assert counts == 1
        assert not await durable.system.has_pending_permission_ask("c")
        assert not await durable.system.has_pending_user_input("c")
        facts = await durable.store.lifecycle_facts("c")
        assert any(
            f.identity == request_id and f.status is LifecycleStatus.answering for f in facts
        )
        assert not any(
            isinstance(e.payload, (PermissionAnsweredEventPayload, UserInputAnsweredEventPayload))
            for e in await durable.events("c")
        )
        answering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await answering
        fail = False
        assert durable.store.resume_recording("c") == 1
        await durable.settle()
        assert await answer() is AnswerFate.refused
        assert counts == 1
        assert (
            sum(
                isinstance(
                    e.payload, (PermissionAnsweredEventPayload, UserInputAnsweredEventPayload)
                )
                for e in await durable.events("c")
            )
            == 1
        )
        assert any(
            f.identity == request_id and f.status is LifecycleStatus.answered
            for f in await durable.store.lifecycle_facts("c")
        )

    _run(exercise)








def test_cancellation_outcome_and_ending_share_one_commit(durable: _Harness) -> None:
    async def exercise() -> None:
        await _start(durable, "c")
        await durable.system.send("c", text_message_content("work"), sender_label="owner")
        fail = True

        def participant(conn: sqlite3.Connection, candidate: LifecycleTransition) -> None:
            if any(
                f.operation is LifecycleOperation.cancel and f.status is LifecycleStatus.accepted
                for f in candidate.facts
            ):
                assert candidate.receipt_sequences
                if fail:
                    raise RuntimeError("cancel outcome commit failure")

        durable.store._transaction_participant = participant
        interruption = asyncio.create_task(durable.system.interrupt("c"))
        await _paused(durable.store)
        assert durable.backend("c").cancellations == 1
        facts = await durable.store.lifecycle_facts("c")
        assert any(
            f.operation is LifecycleOperation.cancel and f.status is LifecycleStatus.intent
            for f in facts
        )
        assert await durable.recorded_endings("c") == ()
        fail = False
        assert durable.store.resume_recording("c") == 1
        await interruption
        facts = await durable.store.lifecycle_facts("c")
        assert any(
            f.operation is LifecycleOperation.cancel and f.status is LifecycleStatus.accepted
            for f in facts
        )
        assert any(
            f.kind is LifecycleKind.turn and f.status is LifecycleStatus.interrupted for f in facts
        )
        assert durable.backend("c").cancellations == 1

    _run(exercise)


def test_restart_reused_provider_id_cannot_accept_old_answer(durable: _Harness) -> None:
    async def exercise() -> None:
        await _start(durable, "c")
        await durable.system.send("c", text_message_content("first"), sender_label="owner")
        old_id = await durable.raise_permission_ask("c")
        await durable.system.shutdown()
        with pytest.raises(RuntimeError, match="shut down"):
            await durable.system.send("c", text_message_content("closed"), sender_label="owner")
        restarted = _Harness(Path(durable.store._db_path))
        try:
            await restarted.system.send("c", text_message_content("second"), sender_label="owner")
            new_id = await restarted.raise_permission_ask("c")
            assert old_id != new_id
            assert (
                await restarted.system.answer_permission_ask("c", old_id, "deny")
            ) is AnswerFate.refused
            assert (
                await restarted.system.answer_permission_ask("c", new_id, "deny")
            ) is AnswerFate.applied
            assert restarted.backend("c").permission_answers == {"ask-1": "deny"}
            assert not any(
                isinstance(e.payload, PermissionAskedEventPayload)
                and e.payload.ask_id == old_id
                and e.sequence > 2
                for e in await restarted.events("c")
            )
        finally:
            await restarted.system.shutdown()

    _run(exercise)






def test_cancelled_shutdown_retains_lease_until_all_local_writers_stop(durable: _Harness) -> None:
    async def exercise() -> None:
        for conversation_id in ("first", "second"):
            await _start(durable, conversation_id)
            await durable.system.send(
                conversation_id, text_message_content("work"), sender_label="owner"
            )
        first = durable.backend("first")
        first.stop_has_begun = asyncio.Event()
        first.stops_wait_for_release = asyncio.Event()
        caller = asyncio.create_task(durable.system.shutdown())
        await asyncio.wait_for(first.stop_has_begun.wait(), 5)
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert not durable.system._runtime_ownership.closed
        assert all(
            state.backend_event_pump is None for state in durable.system._conversations.values()
        )
        assert all(not state.settlement_tasks for state in durable.system._conversations.values())
        with pytest.raises(ConversationRuntimeAlreadyOwned):
            _Harness(Path(durable.store._db_path))
        first.stops_wait_for_release.set()
        await durable.system.shutdown()
        assert durable.system._runtime_ownership.closed
        assert not durable.store._write_operations
        restarted = _Harness(Path(durable.store._db_path))
        try:
            assert not await restarted.system.is_running("second")
        finally:
            await restarted.system.shutdown()

    _run(exercise)
