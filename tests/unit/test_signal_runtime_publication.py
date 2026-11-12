"""Actual runtime status publication through ordinary application saves."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import pytest
from tests.unit import test_conversation_system as controlled

from planner.conversation.backends.contracts import (
    PermissionAnswerWriteFailed,
    TurnEnded,
)
from planner.conversation.contracts import (
    AnswerFate,
    ConversationAttention,
    ConversationBackendKey,
    ConversationStartRequest,
    SignalAttachment,
    SignalBatch,
    SignalSnapshot,
    SignalUnavailable,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    MessageToOwnerEventPayload,
    UserInputAnswer,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
)
from planner.conversation.lifecycle_storage import (
    ConversationRuntimeAlreadyOwned,
    acquire_runtime_ownership,
)
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.publication import SignalPublicationCoordinator
from planner.conversation.runtime_composition import SignalRuntimeComposition, transaction_control
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema


class _SignalHarness(controlled._Harness):
    signal_snapshot: SignalSnapshot


_EnabledRuntime = tuple[
    _SignalHarness,
    SignalPublicationCoordinator,
    list[SignalBatch],
    SignalAttachment,
    asyncio.Runner,
]


@contextmanager
def _enabled_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seed: Callable[[Path], None] | None = None,
) -> Iterator[_EnabledRuntime]:
    path = tmp_path / "runtime.db"
    connection = connect(str(path))
    create_schema(connection)
    connection.close()
    if seed is not None:
        seed(path)
    composition = SignalRuntimeComposition(str(path), 1000)
    composition.bootstrap()
    ownership = composition.ownership
    assert ownership is not None
    coordinator = composition.coordinator
    store = ConversationStore(
        str(path),
        signal_coordinator=coordinator,
        transaction_control_guard=transaction_control,
    )
    monkeypatch.setattr(store, "_connect", composition.connect)
    with monkeypatch.context() as patch:
        patch.setattr(controlled, "ConversationStore", lambda *args, **kwargs: store)
        patch.setattr(
            controlled,
            "SqliteProcessConversationSystem",
            partial(SqliteProcessConversationSystem, runtime_ownership=ownership),
        )
        runtime = _SignalHarness(path)
    assert runtime.system._runtime_ownership is ownership
    composition.transfer_ownership()
    composition.initialize()
    batches: list[SignalBatch] = []
    attachment = coordinator.attach_signal_publication(
        install=lambda connection, snapshot: setattr(runtime, "signal_snapshot", snapshot),
        participate=lambda connection, batch: batches.append(batch),
        unavailable=lambda reason: None,
    )
    # Exercise and shutdown share the same live loop and its owned tasks.
    with asyncio.Runner() as loop:
        try:
            yield runtime, coordinator, batches, attachment, loop
        finally:
            loop.run(runtime.system.shutdown())
            composition.close()
            assert ownership.closed


def _latest_attention(batches: list[SignalBatch]) -> ConversationAttention:
    return next(
        value.attention
        for batch in reversed(batches)
        for value in batch.replacements
        if value.conversation_id == "c"
    )


def test_actual_turn_pending_answer_and_end_share_source_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _enabled_runtime(tmp_path, monkeypatch) as (
        runtime,
        coordinator,
        batches,
        attachment,
        loop,
    ):

        async def exercise() -> None:
            await controlled._start(runtime)
            await runtime.system.send("c", text_message_content("work"), sender_label="owner")
            assert _latest_attention(batches).signal["working"]
            assert await runtime.system.is_running("c")
            assert not await runtime.system.has_pending_permission_ask("c")
            assert not await runtime.system.has_pending_user_input("c")
            permission = await runtime.raise_permission_ask("c")
            request = await runtime.request_user_input("c")
            assert _latest_attention(batches).awaiting_answer
            runtime.backend("c").permission_answer_write_fails = True
            assert (
                await runtime.system.answer_permission_ask("c", permission, "allow-once")
            ) is AnswerFate.refused
            assert await runtime.system.has_pending_permission_ask("c")
            runtime.backend("c").permission_answer_write_fails = False
            assert (
                await runtime.system.answer_permission_ask("c", permission, "deny")
            ) is AnswerFate.applied
            state = runtime.system._conversations["c"]
            assert state.running_turn is not None
            assert state.running_turn.user_input_provider_ids[request.request_id] == "input-1"
            runtime.backend("c").user_input_answer_write_fails = True
            assert (await runtime.system.answer_user_input(
                "c",
                request.request_id,
                (UserInputAnswer("scope", ("Backend",)), UserInputAnswer("timing", ("Now",))),
            )) is AnswerFate.refused
            assert await runtime.system.has_pending_user_input("c")
            runtime.backend("c").user_input_answer_write_fails = False
            assert (await runtime.system.answer_user_input(
                "c",
                request.request_id,
                (
                    UserInputAnswer("scope", ("Backend",)),
                    UserInputAnswer("timing", ("Now",)),
                ),
            )) is AnswerFate.applied
            assert not _latest_attention(batches).awaiting_answer
            assert not await runtime.system.has_pending_permission_ask("c")
            assert not await runtime.system.has_pending_user_input("c")
            await runtime.complete_turn("c")
            attention = _latest_attention(batches)
            assert not attention.signal["working"] and not attention.awaiting_answer
            assert state.running_turn is None
            assert not await runtime.system.is_running("c")
            attachment.validate(attachment.capture_permit())

        loop.run(asyncio.wait_for(exercise(), 8))


def test_status_reads_retain_signal_and_runtime_ownership_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _enabled_runtime(tmp_path, monkeypatch) as (
        runtime,
        coordinator,
        batches,
        attachment,
        loop,
    ):

        async def exercise() -> None:
            await controlled._start(runtime)
            coordinator.invalidate("shutdown")
            for conversation_id in ("c", "missing"):
                for read in (
                    runtime.system.is_running,
                    runtime.system.has_pending_permission_ask,
                    runtime.system.has_pending_user_input,
                ):
                    with pytest.raises(SignalUnavailable):
                        await read(conversation_id)

            await runtime.system.shutdown()
            assert runtime.system._runtime_ownership.closed
            for conversation_id in ("c", "missing"):
                for read in (
                    runtime.system.is_running,
                    runtime.system.has_pending_permission_ask,
                    runtime.system.has_pending_user_input,
                ):
                    with pytest.raises(RuntimeError, match="the conversation runtime is shut down"):
                        await read(conversation_id)

        loop.run(asyncio.wait_for(exercise(), 8))


def test_single_answer_reserves_before_provider_and_cannot_restore_after_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _enabled_runtime(tmp_path, monkeypatch) as (
        runtime,
        coordinator,
        batches,
        attachment,
        loop,
    ):

        async def exercise() -> None:
            await controlled._start(runtime)
            await runtime.system.send("c", text_message_content("work"), sender_label="owner")
            permission = await runtime.raise_permission_ask("c")
            state = runtime.system._conversations["c"]
            running = state.running_turn
            assert running is not None
            entered, release = asyncio.Event(), asyncio.Event()

            async def blocked_send(*args: object, **kwargs: object) -> None:
                assert not _latest_attention(batches).awaiting_answer
                assert not running.pending_permission_ask_ids
                entered.set()
                await release.wait()
                raise PermissionAnswerWriteFailed()

            assert state.child is not None
            monkeypatch.setattr(state.child, "answer_permission_ask", blocked_send)
            answer = asyncio.create_task(
                runtime.system.answer_permission_ask("c", permission, "deny")
            )
            await entered.wait()
            await runtime.system.take_backend_event(
                running.token,
                TurnEnded(ending=ConversationTurnEnding.completed),
            )
            release.set()
            assert await answer is AnswerFate.refused
            assert state.running_turn is None
            assert not await runtime.system.has_pending_permission_ask("c")
            assert await runtime.system.pending_permission_ask_ids("c") == frozenset()
            assert not _latest_attention(batches).awaiting_answer

        loop.run(asyncio.wait_for(exercise(), 8))


def test_startup_reconciles_persisted_pending_but_preserves_unread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def seed(path: Path) -> None:
        store = ConversationStore(str(path))

        async def prepare() -> None:
            await store.create_conversation(
                resolve_conversation_start_request(
                    ConversationStartRequest(
                        conversation_id="historical",
                        backend_key=ConversationBackendKey.hermes,
                        model="fixture",
                        workspace_folder=tmp_path,
                    )
                )
            )
            await store.append_event(
                "historical",
                MessageToOwnerEventPayload(
                    content=text_message_content("Unread"),
                    sender=Principal(PrincipalKind.chief, "chief"),
                    recipient=Principal(PrincipalKind.owner, "owner"),
                    sender_label="Chief",
                ),
                lifecycle=(
                    LifecycleFact(
                        LifecycleKind.request,
                        "old-ask",
                        1,
                        LifecycleOperation.permission,
                        LifecycleStatus.pending,
                    ),
                ),
            )

        asyncio.run(prepare())

    with _enabled_runtime(tmp_path, monkeypatch, seed=seed) as (
        runtime,
        coordinator,
        batches,
        attachment,
        loop,
    ):
        (value,) = runtime.signal_snapshot.values
        assert value.conversation_id == "historical"
        assert value.attention.signal == {"working": False, "message_for_owner": True}
        assert not value.attention.awaiting_answer
        assert not runtime.spawned_conversation_ids
        facts = loop.run(runtime.store.lifecycle_facts("historical"))
        assert facts[0].status is LifecycleStatus.unavailable
        with pytest.raises(ConversationRuntimeAlreadyOwned):
            acquire_runtime_ownership(runtime.store._db_path)
