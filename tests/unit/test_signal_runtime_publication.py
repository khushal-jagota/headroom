"""Actual runtime mutations through an injected producer driver, without core cutover."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from tests.unit import test_conversation_system as controlled
from tests.unit.test_signal_publication import (
    _bind_admission,
    _callback_guard,
    _InjectedConnection,
)

from planner.conversation.backends.contracts import (
    BackendPermissionAsk,
    PermissionAnswerWriteFailed,
)
from planner.conversation.contracts import (
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
    validate_runtime_ownership,
)
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.publication import SignalPublicationCoordinator, persistence
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.database_modules import attach


class _SignalHarness(controlled._Harness):
    signal_snapshot: SignalSnapshot


_EnabledRuntime = tuple[
    _SignalHarness, SignalPublicationCoordinator, list[SignalBatch], SignalAttachment
]


class _RuntimeConnection(_InjectedConnection):
    """Add only SQL commit spellings used by the existing store to the shared fixture."""

    uncertain_commit = False

    def commit(self) -> None:
        if not self.uncertain_commit:
            return super().commit()
        lease, self.lease = self.lease, None
        assert lease is not None
        self.coordinator.finalize_signal(self)
        sqlite3.Connection.commit(self)
        error = RuntimeError("fixture commit result is uncertain")
        lease.settle("uncertain", error)
        lease.release()
        raise error

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        control = sql.strip().upper()
        if control in {"COMMIT", "END"}:
            self.commit()
            return super().execute("SELECT 1 WHERE 0")
        if control == "ROLLBACK":
            self.rollback()
            return super().execute("SELECT 1 WHERE 0")
        return super().execute(sql, parameters)


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
    ownership = acquire_runtime_ownership(str(path))
    coordinator: SignalPublicationCoordinator | None = None

    def source() -> _RuntimeConnection:
        assert coordinator is not None
        connection = sqlite3.connect(path, isolation_level=None, factory=_RuntimeConnection)
        connection.row_factory = sqlite3.Row
        connection.coordinator = coordinator
        attach(connection)
        coordinator.attach_connection(connection)
        return connection

    coordinator = SignalPublicationCoordinator(
        str(path),
        ownership,
        connect=source,
        authority_check=validate_runtime_ownership,
        savepoint_depth=lambda connection: 0,
        transaction_control=_callback_guard,
        bind_admission=_bind_admission,
    )
    with coordinator.bootstrap_authority(ownership) as bootstrap:
        connection = source()
        with _bind_admission(connection, bootstrap):
            connection.execute("BEGIN IMMEDIATE")
            persistence.create_schema(connection)
            persistence.install_source_triggers(connection)
            connection.commit()
        connection.close()
        initialization = coordinator.begin_initialization(bootstrap)
        store = ConversationStore(
            str(path),
            signal_coordinator=coordinator,
            bind_signal_admission=_bind_admission,
            transaction_control_guard=_callback_guard,
            runtime_reconciliation_admission=initialization,
        )
        monkeypatch.setattr(store, "_connect", source)
        with monkeypatch.context() as patch:
            patch.setattr(controlled, "ConversationStore", lambda *args, **kwargs: store)
            patch.setattr(
                controlled,
                "SqliteProcessConversationSystem",
                partial(SqliteProcessConversationSystem, runtime_ownership=ownership),
            )
            runtime = _SignalHarness(path)
        assert runtime.system._runtime_ownership is ownership
        connection = source()
        with _bind_admission(connection, initialization):
            connection.execute("BEGIN IMMEDIATE")
            coordinator.initialize(connection)
            connection.commit()
        connection.close()
    batches: list[SignalBatch] = []
    attachment = coordinator.attach_signal_publication(
        install=lambda connection, snapshot: setattr(runtime, "signal_snapshot", snapshot),
        participate=lambda connection, batch: batches.append(batch),
        unavailable=lambda reason: None,
    )
    try:
        yield runtime, coordinator, batches, attachment
    finally:
        asyncio.run(runtime.system.shutdown())
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
    with _enabled_runtime(tmp_path, monkeypatch) as (runtime, coordinator, batches, attachment):

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
            assert not await runtime.system.answer_permission_ask("c", permission, "allow-once")
            assert await runtime.system.has_pending_permission_ask("c")
            runtime.backend("c").permission_answer_write_fails = False
            assert await runtime.system.answer_permission_ask("c", permission, "deny")
            state = runtime.system._conversations["c"]
            assert state.running_turn is not None
            assert state.running_turn.user_input_provider_ids[request.request_id] == "input-1"
            runtime.backend("c").user_input_answer_write_fails = True
            assert not await runtime.system.answer_user_input(
                "c",
                request.request_id,
                (UserInputAnswer("scope", ("Backend",)), UserInputAnswer("timing", ("Now",))),
            )
            assert await runtime.system.has_pending_user_input("c")
            runtime.backend("c").user_input_answer_write_fails = False
            assert await runtime.system.answer_user_input(
                "c",
                request.request_id,
                (
                    UserInputAnswer("scope", ("Backend",)),
                    UserInputAnswer("timing", ("Now",)),
                ),
            )
            assert not _latest_attention(batches).awaiting_answer
            assert not await runtime.system.has_pending_permission_ask("c")
            assert not await runtime.system.has_pending_user_input("c")
            await runtime.complete_turn("c")
            attention = _latest_attention(batches)
            assert not attention.signal["working"] and not attention.awaiting_answer
            assert state.running_turn is None
            assert not await runtime.system.is_running("c")
            attachment.validate(attachment.capture_permit())

        asyncio.run(asyncio.wait_for(exercise(), 8))


def test_status_reads_retain_signal_and_runtime_ownership_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _enabled_runtime(tmp_path, monkeypatch) as (runtime, coordinator, batches, attachment):

        async def exercise() -> None:
            await controlled._start(runtime)
            coordinator.invalidate()
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

        asyncio.run(asyncio.wait_for(exercise(), 8))


def test_rejected_request_restores_actual_slot_fields_before_gate_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _enabled_runtime(tmp_path, monkeypatch) as (runtime, coordinator, batches, attachment):

        async def exercise() -> None:
            await controlled._start(runtime)
            await runtime.system.send("c", text_message_content("work"), sender_label="owner")
            state = runtime.system._conversations["c"]
            running = state.running_turn
            assert running is not None
            pending, providers = running.pending_permission_ask_ids, running.permission_provider_ids
            original = coordinator._participant

            def reject(connection: sqlite3.Connection, batch: SignalBatch) -> None:
                raise RuntimeError("reject candidate")

            coordinator._participant = reject
            task = asyncio.create_task(
                runtime.system._on_permission_ask_raised(
                    state,
                    running.token,
                    BackendPermissionAsk("provider-ask", "Title", "Detail", ()),
                )
            )
            for _ in range(500):
                if runtime.store.recording_failures("c"):
                    break
                await asyncio.sleep(0.002)
            assert runtime.store.recording_failures("c")
            assert running.pending_permission_ask_ids is pending
            assert running.permission_provider_ids is providers
            assert not pending and not providers
            with pytest.raises(SignalUnavailable):
                attachment.capture_permit()
            connection = runtime.store._connect()
            try:
                sqlite3.Connection.execute(connection, "BEGIN")
                with pytest.raises(SignalUnavailable):
                    coordinator.confirm_repaired(connection)
                sqlite3.Connection.rollback(connection)
            finally:
                connection.close()
            coordinator._participant = original
            runtime.store.resume_recording("c")
            await asyncio.wait_for(task, 5)
            assert running.pending_permission_ask_ids == {"1:permission:provider-ask"}
            assert running.permission_provider_ids == {"1:permission:provider-ask": "provider-ask"}

        asyncio.run(asyncio.wait_for(exercise(), 8))


def test_single_answer_reserves_before_provider_and_cannot_restore_after_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _enabled_runtime(tmp_path, monkeypatch) as (runtime, coordinator, batches, attachment):

        async def exercise() -> None:
            await controlled._start(runtime)
            await runtime.system.send("c", text_message_content("work"), sender_label="owner")
            permission = await runtime.raise_permission_ask("c")
            state = runtime.system._conversations["c"]
            running = state.running_turn
            assert running is not None
            entered, release = asyncio.Event(), asyncio.Event()

            async def blocked_send(*args: object) -> None:
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
            await runtime.system._on_turn_ended(
                state,
                running.token,
                ConversationTurnEnding.completed,
                None,
                None,
            )
            release.set()
            assert not await answer
            assert state.running_turn is None
            assert not running.pending_permission_ask_ids and not running.permission_provider_ids
            assert not _latest_attention(batches).awaiting_answer

        asyncio.run(asyncio.wait_for(exercise(), 8))


def test_uncertain_committed_prompt_recovers_result_without_provider_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _enabled_runtime(tmp_path, monkeypatch) as (runtime, coordinator, batches, attachment):
        original = coordinator.record_result
        failed = False

        def uncertain_prompt(connection: sqlite3.Connection, result: object) -> None:
            nonlocal failed
            original(connection, result)
            if not failed and isinstance(result, dict) and "events" in result:
                failed = True
                assert isinstance(connection, _RuntimeConnection)
                connection.uncertain_commit = True

        monkeypatch.setattr(coordinator, "record_result", uncertain_prompt)

        async def exercise() -> None:
            await controlled._start(runtime)
            delivery = asyncio.create_task(
                runtime.system.send(
                    "c",
                    text_message_content("only once"),
                    sender_label="owner",
                )
            )
            for _ in range(500):
                if runtime.store.recording_failures("c"):
                    break
                await asyncio.sleep(0.002)
            assert runtime.store.recording_failures("c")
            with pytest.raises(SignalUnavailable):
                attachment.capture_permit()
            runtime.store.resume_recording("c")
            await delivery
            assert len(runtime.backend("c").writes) == 1
            events = await runtime.events("c")
            assert sum(event.kind.value == "prompt" for event in events) == 1
            assert runtime.system._conversations["c"].running_turn is not None

        asyncio.run(asyncio.wait_for(exercise(), 8))


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
    ):
        (value,) = runtime.signal_snapshot.values
        assert value.conversation_id == "historical"
        assert value.attention.signal == {"working": False, "message_for_owner": True}
        assert not value.attention.awaiting_answer
        assert not runtime.spawned_conversation_ids
        facts = asyncio.run(runtime.store.lifecycle_facts("historical"))
        assert facts[0].status is LifecycleStatus.unavailable
        with pytest.raises(ConversationRuntimeAlreadyOwned):
            acquire_runtime_ownership(runtime.store._db_path)


def test_departed_prompt_intent_and_publication_roll_back_as_one_owned_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-intent fault cannot commit a prompt, choice, validation or mutation lease."""
    from dataclasses import replace

    from tests.unit.test_current_model_resolution import context, observation, row

    from planner.conversation.contracts import PromptDeliveryMode
    from planner.conversation.events import PromptEventPayload
    from planner.conversation.lifecycle import LifecycleTransition
    from planner.conversation.model_resolution.contracts import SelectionKind
    from planner.conversation.model_resolution.data import ModelResolutionStore
    from planner.conversation.model_resolution.logic.validation import validate_model_selection
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.tickets import data as tickets
    from planner.tickets.validation.data import read_launch_selection

    ticket_ids: list[str] = []

    def seed(path: Path) -> None:
        connection = connect(str(path))
        try:
            ticket = tickets.create_ticket(
                connection,
                title="Owned departure",
                worker_type="coding",
                principal=OWNER_PRINCIPAL,
                now=1000,
                title_max_chars=200,
                employee_backend="codex",
                employee_launch_model="old-exact",
                selection_kind=SelectionKind.concrete,
            )
            connection.execute("UPDATE tickets SET conversation_id='c' WHERE id=?", (ticket.id,))
            connection.commit()
            ticket_ids.append(ticket.id)
        finally:
            connection.close()
        ModelResolutionStore(str(path)).keep_observation(
            observation((row("new-exact", efforts=()),))
        )

    with _enabled_runtime(tmp_path, monkeypatch, seed=seed) as (
        runtime,
        coordinator,
        batches,
        attachment,
    ):

        async def exercise() -> None:
            await controlled._start(
                runtime, backend_key=ConversationBackendKey.codex, model="old-exact"
            )
            await runtime.store.reserve_turn("c")
            connection = runtime.store._connect()
            try:
                original = read_launch_selection(connection, ticket_ids[0])
            finally:
                connection.close()
            requested = replace(original, submitted_token="new-exact", reasoning_effort=None)
            validation = validate_model_selection(
                requested,
                context(),
                observation((row("new-exact", efforts=()),)),
                now=1000,
                attempt_id="attempt-1",
            )
            assert validation.launch_token == "new-exact"

            def snapshot() -> dict[str, list[tuple[object, ...]]]:
                with sqlite3.connect(f"file:{runtime.db_path}?mode=ro", uri=True) as connection:
                    return {
                        table: list(connection.execute(f"SELECT * FROM {table} ORDER BY rowid"))
                        for table in (
                            "conversation_events",
                            "conversations",
                            "tickets",
                            "ticket_launch_validation",
                            "ticket_launch_authority",
                            "executor_command_outcomes",
                            "executor_core_turns",
                            "signal_publication_meta",
                            "signal_publication_values",
                            "signal_publication_operations",
                            "signal_publication_batches",
                        )
                    }

            before = snapshot()

            def fail_after_intent(
                connection: sqlite3.Connection, candidate: LifecycleTransition
            ) -> None:
                assert candidate.receipt_sequences
                assert (
                    connection.execute(
                        "SELECT employee_launch_model FROM tickets WHERE id=?", (ticket_ids[0],)
                    ).fetchone()[0]
                    == "new-exact"
                )
                assert (
                    read_launch_selection(connection, ticket_ids[0]).submitted_token == "new-exact"
                )
                assert (
                    connection.execute(
                        "SELECT validation_json FROM ticket_launch_validation WHERE ticket_id=?",
                        (ticket_ids[0],),
                    ).fetchone()[0]
                    is not None
                )
                assert not list(connection.execute("SELECT * FROM ticket_launch_authority"))
                raise RuntimeError("fault after exact intent before commit")

            runtime.store._transaction_participant = fail_after_intent
            with pytest.raises(RuntimeError, match="after exact intent"):
                await runtime.store.append_delivered_prompt(
                    "c",
                    prompt=PromptEventPayload(
                        content=text_message_content("Already departed"),
                        sender_label="owner",
                        mode=PromptDeliveryMode.queue,
                    ),
                    model_change=None,
                    original_ticket_selection=original,
                    requested_ticket_validation=validation,
                )
            assert snapshot() == before
            assert not await runtime.store.read_events_after("c", 0)

        asyncio.run(asyncio.wait_for(exercise(), 8))
