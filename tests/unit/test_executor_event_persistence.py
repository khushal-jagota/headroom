"""Worker settlement through real receipt transactions and active Signal admission."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import pytest
from tests.unit.test_conversation_api import _Harness, _start
from tests.unit.test_conversation_executor_publication import _envelope, _take_event

from planner.conversation.backend_event_contracts import AcceptedEvent, EventTaken
from planner.conversation.events import ConversationEventKind
from planner.conversation.message_content import text_message_content
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import ChangeSignallingConnection, connect, create_schema
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    BackendEvent,
    BackendEventMethod,
    EnvelopeKind,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    as_object,
)
from planner.executor.vps_inbox import InboxStore


@asynccontextmanager
async def _case(
    root: Path, signal: bool
) -> AsyncIterator[tuple[_Harness, InboxStore, ExecutorIdentity, BackendEvent, QueuedEnvelope]]:
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
        store = InboxStore(str(path))
        identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "proof", 1)
        store.runs.begin_connection(identity, 1000)
        store.runs.register_child(child, "chief", "chief", 1000)
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

            def accept(connection: sqlite3.Connection, take: EventTaken) -> AcceptedEvent:
                def blocked(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    result = take()
                    writer_threads.append(threading.get_ident())
                    entered.set()
                    if not release.wait(3):
                        raise RuntimeError("controlled release failed")
                    return result

                receipt, taken = store.accept_in_transaction(
                    connection, envelope, blocked, 1001, identity=identity
                )
                return AcceptedEvent(as_object(receipt), taken)

            # A regressed synchronous writer cannot deadlock the test process.
            watchdog = threading.Timer(2, release.set)
            watchdog.start()
            caller = asyncio.create_task(_take_event(harness.system, event, envelope, accept))
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
                assert not any(
                    row.kind is ConversationEventKind.tool_call_started
                    for row in await harness.store.read_events_after("c", 0)
                )
                release.set()
                await asyncio.gather(*owned)
                assert not state.lock.locked()
                receipt = store.lookup_receipt(envelope.sender_id, envelope.submission_id)
                assert receipt is not None and receipt.disposition is ReceiptDisposition.accepted
                assert await _take_event(harness.system, event, envelope, accept) == receipt
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

            def accept(connection: sqlite3.Connection, take: EventTaken) -> AcceptedEvent:
                def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    if outcome == "refused":
                        take()  # The receipt savepoint must roll back an already-written effect.
                        raise PlannerError(ErrorCode.validation, "controlled refusal", {})
                    return take()

                receipt, taken = store.accept_in_transaction(
                    connection, envelope, effect, 1001, identity=identity
                )
                return AcceptedEvent(as_object(receipt), taken)

            if outcome == "replay":
                await _take_event(harness.system, event, envelope, accept)
            coordinator = harness.store.signal_coordinator
            assert coordinator is not None
            original_record = coordinator.record_result
            original_commit = ChangeSignallingConnection.commit
            target: list[sqlite3.Connection] = []
            seen: list[AcceptedEvent] = []
            original_encode = harness.store._encode_signal_result

            def encode(result: object) -> object:
                if isinstance(result, AcceptedEvent):
                    seen.append(result)
                return original_encode(result)

            def record(connection: sqlite3.Connection, result: object) -> None:
                original_record(connection, result)
                if isinstance(result, dict) and "accepted_event" in result and not target:
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
                task = asyncio.create_task(_take_event(harness.system, event, envelope, accept))
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
            assert seen[0].taken is (outcome == "accepted")
            assert bool(seen[0].written) is (outcome == "accepted")
            events = await harness.store.read_events_after("c", 0)
            assert sum(row.kind is ConversationEventKind.tool_call_started for row in events) == (
                0 if outcome == "refused" else 1
            )
            assert await _take_event(harness.system, event, envelope, accept) == receipt

    asyncio.run(asyncio.wait_for(exercise(), 10))


def test_local_callbacks_and_mac_inbox_produce_identical_transcripts(tmp_path: Path) -> None:
    """Compare the real callback queue and inbox, including request reuse and closure."""
    from dataclasses import fields
    from typing import cast

    from planner.conversation.backends.contracts import (
        AgentMessageCompletedArguments,
        BackendPermissionAsk,
        BackendUserInputRequest,
        ContextCompactedArguments,
        DecodedBackendEventArguments,
        PermissionAskRaisedArguments,
        PlanUpdatedArguments,
        ProviderSelectionReportedArguments,
        TokenUsageReportedArguments,
        ToolCallFinishedArguments,
        ToolCallStartedArguments,
        TurnEndedArguments,
        UserInputFailedArguments,
        UserInputRequestedArguments,
    )
    from planner.conversation.events import (
        ConversationTurnEnding,
        PermissionAskOption,
        ToolCallStatus,
        UserInputQuestion,
    )
    from planner.core.clock import RealClock
    from planner.executor.vps_transport import VpsExecutor

    async def exercise() -> None:
        transcripts = []
        for path in ("local", "mac"):
            root = tmp_path / path
            root.mkdir()
            async with _case(root, True) as (harness, store, identity, first, _):
                state = harness.system._conversations["c"]
                await state.backend_event_queue.join()
                token = first.turn
                assert token is not None
                sink = harness.backends["c"].sink
                assert sink is not None
                endpoint = VpsExecutor(str(harness.db_path), RealClock())
                endpoint.bind_conversations(harness.system)
                endpoint.runs.identity = identity
                permission = PermissionAskRaisedArguments(
                    ask=BackendPermissionAsk(
                        "ask", "Run?", None, (PermissionAskOption("allow", "Allow", "allow_once"),)
                    )
                )
                sequence: tuple[tuple[BackendEventMethod, DecodedBackendEventArguments], ...] = (
                    (
                        BackendEventMethod.provider_selection_reported,
                        ProviderSelectionReportedArguments(
                            provider=state.record.backend_key,
                            source_kind="response",
                            model="reported-model",
                            child_lifetime="test-child",
                        ),
                    ),
                    (
                        BackendEventMethod.tool_call_started,
                        ToolCallStartedArguments(
                            tool_call_id="tool",
                            title="Work",
                            tool_kind="other",
                        ),
                    ),
                    (BackendEventMethod.plan_updated, PlanUpdatedArguments(entries=())),
                    (
                        BackendEventMethod.token_usage_reported,
                        TokenUsageReportedArguments(input_tokens=42),
                    ),
                    (BackendEventMethod.permission_ask_raised, permission),
                    (BackendEventMethod.permission_ask_raised, permission),
                    (
                        BackendEventMethod.user_input_requested,
                        UserInputRequestedArguments(
                            request=BackendUserInputRequest(
                                "question",
                                (UserInputQuestion("q", "Scope", "Choose", (), False, True),),
                            )
                        ),
                    ),
                    (
                        BackendEventMethod.user_input_failed,
                        UserInputFailedArguments(request_id="question", detail="withdrawn"),
                    ),
                    (
                        BackendEventMethod.tool_call_finished,
                        ToolCallFinishedArguments(
                            tool_call_id="tool", tool_call_status=ToolCallStatus.completed
                        ),
                    ),
                    (
                        BackendEventMethod.agent_message_completed,
                        AgentMessageCompletedArguments(
                            content=text_message_content("backend prose")
                        ),
                    ),
                    (BackendEventMethod.context_compacted, ContextCompactedArguments()),
                    (
                        BackendEventMethod.turn_ended,
                        TurnEndedArguments(ending=ConversationTurnEnding.completed),
                    ),
                )
                for index, (method, arguments) in enumerate(sequence, 1):
                    if path == "local":
                        await getattr(sink, method.value)(
                            token,
                            **{
                                field.name: getattr(arguments, field.name)
                                for field in fields(arguments)
                            },
                        )
                        await state.backend_event_queue.join()
                    else:
                        from planner.executor.protocol import encode_value

                        event = BackendEvent(
                            first.child, method, token, cast(JsonObject, encode_value(arguments))
                        )
                        envelope = _envelope(index, EnvelopeKind.backend_event, event)
                        receipt = await endpoint.inbox.accept(envelope, identity)
                        assert receipt.disposition is ReceiptDisposition.accepted
                        assert await endpoint.inbox.accept(envelope, identity) == receipt
                rows = await harness.store.read_events_after("c", 0)
                transcripts.append([(row.sequence, row.kind, row.payload) for row in rows])
                assert state.running_turn is None
        assert transcripts[0] == transcripts[1]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_stale_events_receive_durable_ignored_acceptance_without_rows_or_live_frames(
    tmp_path: Path,
) -> None:
    from dataclasses import replace

    from planner.core.clock import RealClock
    from planner.executor.vps_transport import VpsExecutor

    async def exercise() -> None:
        async with _case(tmp_path, True) as (harness, store, identity, first, _):
            state = harness.system._conversations["c"]
            await state.backend_event_queue.join()
            assert first.turn is not None
            token = replace(first.turn, turn_number=first.turn.turn_number + 1)
            original_turn = state.running_turn
            original_record = await harness.store.read_conversation("c")
            original_rows = await harness.store.read_events_after("c", 0)
            endpoint = VpsExecutor(str(harness.db_path), RealClock())
            endpoint.bind_conversations(harness.system)
            endpoint.runs.identity = identity
            with harness.live_tail.subscribe("c") as tail:
                sequence: tuple[tuple[BackendEventMethod, JsonObject], ...] = (
                    (first.method, first.arguments),
                    (BackendEventMethod.context_compacted, {}),
                    (
                        BackendEventMethod.vendor_session_cursor_rebound,
                        {"vendor_session_cursor": "stale-session"},
                    ),
                    (BackendEventMethod.agent_message_delta, {"text_delta": "stale"}),
                    (BackendEventMethod.turn_ended, {"ending": "completed"}),
                )
                for index, (method, args) in enumerate(sequence, 1):
                    event = BackendEvent(first.child, method, token, args)
                    envelope = _envelope(index, EnvelopeKind.backend_event, event)
                    receipt = await endpoint.inbox.accept(envelope, identity)
                    assert receipt.disposition is ReceiptDisposition.accepted
                    assert receipt.result == {"applied": False}
                    assert (
                        store.lookup_receipt(envelope.sender_id, envelope.submission_id) == receipt
                    )
                    assert await endpoint.inbox.accept(envelope, identity) == receipt
                assert tail._items.empty()
            assert await harness.store.read_conversation("c") == original_record
            assert await harness.store.read_events_after("c", 0) == original_rows
            assert state.running_turn is original_turn

    asyncio.run(asyncio.wait_for(exercise(), 10))
