"""Worker settlement through real receipt transactions and active Signal admission."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from tests.unit.test_conversation_api import _Harness, _start
from tests.unit.test_conversation_executor_publication import _envelope

from planner.conversation.backend_event_contracts import ExecutorEffectResult
from planner.conversation.events import ConversationEventKind
from planner.conversation.message_content import text_message_content
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import ChangeSignallingConnection, connect, create_schema
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AcceptanceReceipt,
    BackendEvent,
    BackendEventMethod,
    ChildReference,
    EnvelopeKind,
    ExecutorIdentity,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    TransactionalWriter,
)
from planner.executor.store import ExecutorStore


@asynccontextmanager
async def _case(
    root: Path, signal: bool
) -> AsyncIterator[tuple[_Harness, ExecutorStore, ExecutorIdentity, BackendEvent, QueuedEnvelope]]:
    async with AsyncExitStack() as cleanup:
        path = root / "event.db"
        composition = SignalRuntimeComposition(str(path), 1000) if signal else None
        if composition is not None:
            cleanup.callback(composition.close)
            composition.bootstrap()
        else:
            connection = connect(str(path))
            create_schema(connection)
            connection.close()
        harness = _Harness(path, signal_composition=composition, cleanup=cleanup)
        if composition is not None:
            composition.initialize()
            attachment = composition.coordinator.attach_signal_publication(
                install=lambda connection, snapshot: None,
                participate=lambda connection, batch: None,
                unavailable=lambda reason: None,
            )
            cleanup.callback(attachment.close)
        async with harness.client() as client:
            await _start(client, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        state = harness.system._conversations["c"]
        assert state.running_turn is not None
        child = ChildReference("proof", "child", "c")
        state.executor_child_reference = child
        store = ExecutorStore(str(path))
        identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "proof", 1)
        store.begin_connection(identity, 1000)
        store.register_child(child, "chief", "chief", 1000)
        event = BackendEvent(
            child,
            BackendEventMethod.tool_call_started,
            state.running_turn.token,
            {"tool_call_id": "tool", "title": "Work", "tool_kind": "other", "detail": None},
        )
        yield harness, store, identity, event, _envelope(1, EnvelopeKind.backend_event, event)


@pytest.mark.parametrize("signal", [False, True])
def test_worker_progress_and_cancellation_retain_lock_through_finalization(
    tmp_path: Path, signal: bool
) -> None:
    async def exercise() -> None:
        async with _case(tmp_path, signal) as (harness, store, identity, event, envelope):
            entered, release = threading.Event(), threading.Event()
            loop_thread = threading.get_ident()
            writer_threads: list[int] = []

            def accept(
                row: QueuedEnvelope, write: TransactionalWriter, **publication: Any
            ) -> AcceptanceReceipt:
                def blocked(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    result = write(connection, envelope)
                    writer_threads.append(threading.get_ident())
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("controlled release failed")
                    return result

                return store.accept(row, blocked, 1001, identity=identity, **publication)

            # A regressed synchronous writer cannot deadlock the test process.
            watchdog = threading.Timer(2, release.set)
            watchdog.start()
            caller = asyncio.create_task(
                harness.system.receive_remote_backend_event(event, envelope, accept)
            )
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                # This continuation is unrelated event-loop work during the controlled write.
                assert not release.is_set()
                assert writer_threads[0] != loop_thread
                caller.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await caller
                state = harness.system._conversations["c"]
                assert state.lock.locked()
                owned = tuple(state.settlement_tasks)
                assert owned and not all(task.done() for task in owned)
                assert store.lookup_receipt(envelope.sender_id, envelope.submission_id) is None
                release.set()
                await asyncio.gather(*owned)
                assert not state.lock.locked()
                receipt = store.lookup_receipt(envelope.sender_id, envelope.submission_id)
                assert receipt is not None and receipt.disposition is ReceiptDisposition.accepted
                assert (
                    await harness.system.receive_remote_backend_event(event, envelope, accept)
                    == receipt
                )
                events = await harness.store.read_events_after("c", 0)
                assert (
                    sum(row.kind is ConversationEventKind.tool_call_started for row in events) == 1
                )
            finally:
                release.set()
                watchdog.cancel()
                watchdog.join()

    asyncio.run(asyncio.wait_for(exercise(), 10))


@pytest.mark.parametrize("outcome", ["accepted", "replay", "refused"])
def test_ambiguous_commit_recovers_typed_result_for_every_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    async def exercise() -> None:
        async with _case(tmp_path, True) as (harness, store, identity, event, envelope):

            def accept(
                row: QueuedEnvelope, write: TransactionalWriter, **publication: Any
            ) -> AcceptanceReceipt:
                def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    if outcome == "refused":
                        raise PlannerError(ErrorCode.validation, "controlled refusal", {})
                    return write(connection, envelope)

                return store.accept(row, effect, 1001, identity=identity, **publication)

            if outcome == "replay":
                await harness.system.receive_remote_backend_event(event, envelope, accept)
            coordinator = harness.store.signal_coordinator
            assert coordinator is not None
            original_record = coordinator.record_result
            original_commit = ChangeSignallingConnection.commit
            target: list[sqlite3.Connection] = []
            seen: list[ExecutorEffectResult] = []
            original_encode = harness.store._encode_signal_result

            def encode(result: object) -> object:
                if isinstance(result, ExecutorEffectResult):
                    seen.append(result)
                return original_encode(result)

            def record(connection: sqlite3.Connection, result: object) -> None:
                original_record(connection, result)
                if isinstance(result, dict) and "executor_effect" in result and not target:
                    target.append(connection)

            def commit(connection: ChangeSignallingConnection) -> None:
                original_commit(connection)
                if target and connection is target[0]:
                    target.clear()
                    raise RuntimeError("caller lost the commit result")

            with monkeypatch.context() as fault:
                fault.setattr(coordinator, "record_result", record)
                fault.setattr(harness.store, "_encode_signal_result", encode)
                fault.setattr(ChangeSignallingConnection, "commit", commit)
                task = asyncio.create_task(
                    harness.system.receive_remote_backend_event(event, envelope, accept)
                )
                for _ in range(500):
                    if harness.store.recording_failures("c"):
                        break
                    await asyncio.sleep(0.002)
                assert harness.store.recording_failures("c")
                assert not task.done()
                # Disable the injected loss before explicit reconciliation resumes the owner.
                fault.setattr(ChangeSignallingConnection, "commit", original_commit)
                harness.store.resume_recording("c")
                receipt = await asyncio.wait_for(task, 5)
            assert receipt.disposition.value == ("refused" if outcome == "refused" else "accepted")
            assert len(seen) == 1
            assert seen[0].fresh is (outcome == "accepted")
            assert bool(seen[0].written) is (outcome == "accepted")
            events = await harness.store.read_events_after("c", 0)
            assert sum(row.kind is ConversationEventKind.tool_call_started for row in events) == (
                0 if outcome == "refused" else 1
            )
            assert (
                await harness.system.receive_remote_backend_event(event, envelope, accept)
                == receipt
            )

    asyncio.run(asyncio.wait_for(exercise(), 10))


@pytest.mark.parametrize("path", ["local", "remote"])
@pytest.mark.parametrize(
    ("ending", "confirmed", "expected_compaction"),
    [("completed", False, "not_compacted"), ("completed", True, None), ("failed", False, None)],
)
def test_common_turn_ending_preserves_compaction_and_missing_reply(
    tmp_path: Path, path: str, ending: str, confirmed: bool, expected_compaction: str | None
) -> None:
    """The formerly separate paths retain the same domain outcomes and safe failure text."""
    from planner.conversation.events import ConversationTurnEnding, TurnEndedEventPayload
    from planner.core.contracts import CHIEF_PRINCIPAL

    async def exercise() -> None:
        async with _case(tmp_path, True) as (harness, store, identity, event, envelope):
            state = harness.system._conversations["c"]
            running = state.running_turn
            assert running is not None
            running.automatic_compaction = True
            running.compaction_confirmed = confirmed
            running.prompt_senders = {CHIEF_PRINCIPAL: None}
            error = "  failure\n detail  " if ending == "failed" else None
            if path == "local":
                await harness.system._on_turn_ended(
                    state, running.token, ConversationTurnEnding(ending), error, None
                )
            else:
                finished = BackendEvent(
                    event.child,
                    BackendEventMethod.turn_ended,
                    running.token,
                    {"ending": ending, "error_summary": error, "standard_error_tail": None},
                )
                row = _envelope(1, EnvelopeKind.backend_event, finished)

                def accept(
                    row: QueuedEnvelope, write: TransactionalWriter, **publication: Any
                ) -> AcceptanceReceipt:
                    return store.accept(row, write, 1001, identity=identity, **publication)

                await harness.system.receive_remote_backend_event(finished, row, accept)
            events = await harness.store.read_events_after("c", 0)
            endings = [
                row.payload for row in events if isinstance(row.payload, TurnEndedEventPayload)
            ]
            assert len(endings) == 1
            assert endings[0].ending is ConversationTurnEnding(ending)
            result = endings[0].automatic_compaction_result
            assert (None if result is None else result.value) == expected_compaction
            assert endings[0].error_summary == ("failure detail" if ending == "failed" else None)
            assert (
                sum(row.kind is ConversationEventKind.explicit_reply_missing for row in events) == 1
            )
            assert state.running_turn is None

    asyncio.run(asyncio.wait_for(exercise(), 10))
