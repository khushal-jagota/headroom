"""Native receipts join retained lifecycle and real runtime publication atomically.

Separate branch tests cannot prove this combined boundary. Reuse the HTTP/core
harness and production Signal composition. No browser, native host, or provider runs.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, cast

import pytest
from tests.unit.test_conversation_api import _Harness, _start

from planner.conversation.backend_event_contracts import (
    AcceptedEvent,
    CommitParticipant,
    EventTaken,
)
from planner.conversation.backends.contracts import BackendPermissionAsk, BackendUserInputRequest
from planner.conversation.contracts import SignalBatch
from planner.conversation.events import PermissionAskOption, UserInputAnswer, UserInputQuestion
from planner.conversation.lifecycle import LifecycleStatus, LifecycleTransition
from planner.conversation.message_content import text_message_content
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.conversation.system import SqliteProcessConversationSystem
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    AcceptanceReceipt,
    AnswerApplication,
    BackendEvent,
    BackendEventMethod,
    EnvelopeKind,
    JsonObject,
    QueuedEnvelope,
    as_object,
    decode_event_arguments,
    encode_value,
    envelope_payload_digest,
)
from planner.executor.vps_inbox import InboxStore


def _envelope(sequence: int, kind: EnvelopeKind, value: object) -> QueuedEnvelope:
    payload = cast(JsonObject, encode_value(value))
    submission = f"native-{sequence}"
    return QueuedEnvelope(
        "native",
        "c",
        sequence,
        submission,
        kind,
        payload,
        envelope_payload_digest(
            payload,
            (),
            sender_id="native",
            conversation_id="c",
            sequence=sequence,
            submission_id=submission,
            kind=kind,
        ),
    )


async def _take_event(
    system: SqliteProcessConversationSystem,
    event: BackendEvent,
    envelope: QueuedEnvelope,
    in_commit: CommitParticipant,
) -> AcceptanceReceipt:
    result = await system.take_backend_event(
        event.turn,
        decode_event_arguments(event),
        in_commit=in_commit,
        conversation_id=event.child.conversation_id,
    )
    from pydantic import TypeAdapter

    return TypeAdapter(AcceptanceReceipt).validate_python(result.result)


@pytest.mark.parametrize("kind", list(AskKind))
def test_native_request_receipt_runtime_publication_and_application_share_boundary(
    tmp_path: Path, kind: AskKind
) -> None:
    async def exercise() -> None:
        async with AsyncExitStack() as cleanup:
            composition = SignalRuntimeComposition(str(tmp_path / "mixed.db"), 1000)
            cleanup.callback(composition.close)
            composition.bootstrap()
            harness = _Harness(
                tmp_path / "mixed.db", signal_composition=composition, cleanup=cleanup
            )
            composition.initialize()
            batches: list[SignalBatch] = []
            attachment = composition.coordinator.attach_signal_publication(
                install=lambda conn, snapshot: None,
                participate=lambda conn, batch: batches.append(batch),
                unavailable=lambda reason: None,
            )
            cleanup.callback(attachment.close)
            async with harness.client() as client:
                await _start(client, "c")
            await harness.system.send("c", text_message_content("work"), sender_label="owner")
            state = harness.system._conversations["c"]
            assert state.running_turn is not None
            token = state.running_turn.token
            child = ChildReference("native-process", "native-child", "c")
            state.executor_child_reference = child
            store = InboxStore(str(harness.db_path))
            identity = ExecutorIdentity(
                PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "native-process", 1
            )
            store.runs.begin_connection(identity, 100)
            store.runs.register_child(child, "chief", "chief", 100)
            ask = AskReference(child, token, "reused-provider-id", kind)
            questions = (UserInputQuestion("q", "Scope", "Choose", (), False, True),)
            arguments = (
                {
                    "ask": encode_value(
                        BackendPermissionAsk(
                            ask.ask_id,
                            "Run?",
                            None,
                            (PermissionAskOption("allow", "Allow", "allow_once"),),
                        )
                    )
                }
                if kind is AskKind.permission
                else {"request": encode_value(BackendUserInputRequest(ask.ask_id, questions))}
            )
            event = BackendEvent(
                child,
                BackendEventMethod.permission_ask_raised
                if kind is AskKind.permission
                else BackendEventMethod.user_input_requested,
                token,
                arguments,
            )
            envelope = _envelope(1, EnvelopeKind.backend_event, event)

            def accept(connection: sqlite3.Connection, take: EventTaken) -> AcceptedEvent:
                def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    conn = connection
                    result = take()
                    if kind is AskKind.permission:
                        store.runs.publish_ask_in_transaction(conn, ask, ("allow",), 101)
                    else:
                        store.runs.publish_user_input_request_in_transaction(
                            conn, ask, questions, 101
                        )
                    return result

                receipt, taken = store.accept_in_transaction(
                    connection, envelope, effect, 101, identity=identity
                )
                return AcceptedEvent(as_object(receipt), taken)

            def fail(conn: sqlite3.Connection, candidate: LifecycleTransition) -> None:
                assert conn.in_transaction
                assert any(f.provider_identity == ask.ask_id for f in candidate.facts)
                raise RuntimeError("mixed participant refused")

            harness.store._transaction_participant = fail
            pending = asyncio.create_task(_take_event(harness.system, event, envelope, accept))
            for _ in range(500):
                if harness.store.recording_failures("c"):
                    break
                await asyncio.sleep(0.002)
            assert harness.store.recording_failures("c")
            assert store.lookup_receipt("native", "native-1") is None
            assert state.running_turn is not None
            assert not state.running_turn.pending_permission_ask_ids
            assert not state.running_turn.pending_user_input_questions
            harness.store._transaction_participant = None
            harness.store.resume_recording("c")
            receipt = await asyncio.wait_for(pending, 5)
            assert await _take_event(harness.system, event, envelope, accept) == receipt
            public_id = f"{token.turn_number}:{kind.value}:{ask.ask_id}"
            facts = await harness.store.lifecycle_facts("c")
            assert any(
                f.identity == public_id and f.status is LifecycleStatus.pending for f in facts
            )
            latest = next(
                v.attention
                for b in reversed(batches)
                for v in b.replacements
                if v.conversation_id == "c"
            )
            assert latest.awaiting_answer
            context = store.runs.turn_context("c")
            assert context is not None
            assert ask.ask_id in (
                context.pending_permission_ask_ids
                if kind is AskKind.permission
                else context.pending_user_input_ids
            )
            store.runs.mark_lost(identity, 102)
            choice = (
                store.runs.choose_answer(ask, "allow", 1, 103)
                if kind is AskKind.permission
                else store.runs.choose_user_input_answer(
                    ask, (UserInputAnswer("q", ("answer",)),), 1, 103
                )
            )
            evidence = store.runs.ask_evidence(ask)
            assert evidence is not None and evidence.applied_at_mac_ms is None
            application = AnswerApplication(choice.answer_id, ask, 104)
            applied_envelope = _envelope(2, EnvelopeKind.answer_application, application)

            def accept_application(
                connection: sqlite3.Connection, take: EventTaken
            ) -> AcceptedEvent:
                receipt, taken = store.accept_in_transaction(
                    connection,
                    applied_envelope,
                    lambda connection, envelope: take(),
                    105,
                    identity=identity,
                )
                return AcceptedEvent(as_object(receipt), taken)

            # Reconnect the same process with a fresh generation before native evidence arrives.
            identity = ExecutorIdentity(
                PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "native-process", 2
            )
            store.runs.begin_connection(identity, 104)
            applied = await harness.system.receive_remote_answer_application(
                application,
                accept_application,
                store.runs.record_application_in_transaction,
                store.runs.public_request_id(application.ask),
            )
            assert (
                await harness.system.receive_remote_answer_application(
                    application,
                    accept_application,
                    store.runs.record_application_in_transaction,
                    store.runs.public_request_id(application.ask),
                )
                == applied
            )
            assert not await harness.system.has_pending_permission_ask("c")
            assert not await harness.system.has_pending_user_input("c")
            latest = next(
                v.attention
                for b in reversed(batches)
                for v in b.replacements
                if v.conversation_id == "c"
            )
            assert not latest.awaiting_answer
            facts = await harness.store.lifecycle_facts("c")
            assert any(
                f.identity == public_id and f.status is LifecycleStatus.answered for f in facts
            )
            events = await harness.store.read_events_after("c", 0)
            assert (
                sum(
                    getattr(e.payload, "ask_id", None) == public_id
                    or getattr(e.payload, "request_id", None) == public_id
                    for e in events
                )
                == 2
            )

    asyncio.run(exercise())


def test_restart_closes_native_request_before_initial_signal_snapshot(
    tmp_db: sqlite3.Connection,
) -> None:
    """A VPS restart closes callbacks before the first attention snapshot."""
    from tests.unit.test_executor_native_runtime import canonical_snapshot

    from planner.conversation.contracts import SignalSnapshot
    from planner.executor.protocol import ChildCommand, CommandOutcome
    from planner.executor.vps_remote_child import MacRemoteChild

    path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))

    async def exercise() -> None:
        source = _Harness(path)
        async with source.client() as client:
            await _start(client, "c")
        await source.system.send("c", text_message_content("work"), sender_label="owner")
        public_id = await source.raise_permission_ask("c")
        state = source.system._conversations["c"]
        assert state.running_turn is not None
        child = ChildReference("surviving-process", "surviving-child", "c")

        async def no_native_dispatch(command: ChildCommand) -> CommandOutcome:
            raise AssertionError("VPS restart must not dispatch native work")

        state.child = MacRemoteChild(child, no_native_dispatch, lambda start: canonical_snapshot())
        metadata = source.system._executor_turn_metadata(state, state.running_turn)
        assert metadata is not None
        await source.store.save_executor_turn("c", metadata)
        await source.runtime.shutdown()
        async with AsyncExitStack() as cleanup:
            composition = SignalRuntimeComposition(str(path), 1000)
            cleanup.callback(composition.close)
            composition.bootstrap()
            restored = _Harness(path, signal_composition=composition, cleanup=cleanup)
            # Restore before any API request or inventory callback loads the conversation.
            assert "c" in restored.system._conversations
            assert restored.system._conversations["c"].child is None
            composition.initialize()
            snapshots: list[SignalSnapshot] = []
            attachment = composition.coordinator.attach_signal_publication(
                install=lambda conn, snapshot: snapshots.append(snapshot),
                participate=lambda conn, batch: None,
                unavailable=lambda reason: None,
            )
            cleanup.callback(attachment.close)
            assert not next(
                v for v in snapshots[0].values if v.conversation_id == "c"
            ).attention.awaiting_answer
            assert await restored.system.pending_permission_ask_ids("c") == frozenset()
            assert any(
                f.identity == public_id and f.status is LifecycleStatus.unavailable
                for f in await restored.store.lifecycle_facts("c")
            )
            assert await restored.store.select_deployment_recoveries() == {}

    asyncio.run(exercise())


def test_reserved_turn_exceeds_native_history_without_lifecycle_facts(
    tmp_db: sqlite3.Connection,
) -> None:
    """A database from the feature branch cannot reuse an earlier native turn number."""
    import json

    from planner.conversation.backends.contracts import TurnToken

    path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))

    async def exercise() -> None:
        harness = _Harness(path)
        try:
            async with harness.client() as client:
                await _start(client, "c")
            await harness.store.save_executor_turn(
                "c",
                (
                    json.dumps(encode_value(ChildReference("native", "child", "c"))),
                    json.dumps(encode_value(TurnToken("c", 57))),
                    json.dumps({"ended": True}),
                ),
            )
            assert await harness.store.lifecycle_facts("c") == ()
            assert await harness.store.reserve_turn("c") == 58
        finally:
            await harness.runtime.shutdown()

    asyncio.run(exercise())


def test_refused_evidence_between_cleanup_precheck_and_commit_keeps_core_available(
    tmp_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.test_executor_native_runtime import canonical_snapshot

    from planner.core.contracts import ErrorCode, PlannerError
    from planner.executor.protocol import (
        ChildCommand,
        ChildOperation,
        CommandDisposition,
        CommandOutcome,
        ReceiptDisposition,
    )
    from planner.executor.vps_remote_child import MacRemoteChild

    async def exercise() -> None:
        path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
        harness = _Harness(path)
        try:
            async with harness.client() as client:
                await _start(client, "c")
            await harness.system.send("c", text_message_content("work"), sender_label="owner")
            state = harness.system._conversations["c"]
            assert state.running_turn is not None
            reference = ChildReference("native-process", "cleanup-child", "c")
            token = state.running_turn.token
            native = InboxStore(str(path))
            identity = ExecutorIdentity(
                PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 1
            )
            native.runs.begin_connection(identity, 1)
            native.runs.register_child(reference, "chief", "chief", 1)

            stops: list[str] = []

            async def stop_result(command: ChildCommand) -> CommandOutcome:
                if command.operation is ChildOperation.stop:
                    stops.append(command.command_id)
                return CommandOutcome(
                    command.command_id, reference, CommandDisposition.succeeded, {}
                )

            state.child = MacRemoteChild(reference, stop_result, lambda start: canonical_snapshot())
            state.executor_child_reference = reference
            metadata = harness.system._executor_turn_metadata(state, state.running_turn)
            assert metadata is not None
            await harness.store.save_executor_turn("c", metadata)
            transition = harness.store.transition_lifecycle
            guarded = asyncio.Event()

            async def race_receipt(conversation_id: str, facts: Any, **options: Any) -> bool:
                if options.get("retired_child") is not None:
                    # The core already read the clean marker. The next canonical
                    # receipt wins before the atomic closure writer starts.
                    assert not native.runs.cleanup_evidence_uncertain(reference)
                    event = BackendEvent(reference, BackendEventMethod.turn_ended, token, {})
                    queued = _envelope(1, EnvelopeKind.backend_event, event)

                    def refuse(
                        connection: sqlite3.Connection, envelope: QueuedEnvelope
                    ) -> JsonObject:
                        raise PlannerError(ErrorCode.validation, "required final event refused", {})

                    receipt = native.accept(queued, refuse, 100, identity=identity)
                    assert receipt.disposition is ReceiptDisposition.refused
                    guarded.set()
                return await transition(conversation_id, facts, **options)

            monkeypatch.setattr(harness.store, "transition_lifecycle", race_receipt)
            await asyncio.wait_for(harness.system.kill("c"), 2)
            assert guarded.is_set()
            assert not state.lock.locked()
            assert state.child is not None and state.child_is_quarantined
            assert state.executor_child_reference == reference
            assert native.runs.child_assignment(reference) is not None
            assert native.runs.cleanup_evidence_uncertain(reference)
            assert harness.store.recording_failures("c") == ()
            await asyncio.wait_for(harness.system.kill("c"), 2)
            assert state.initial_shutdown_task is not None
            await asyncio.wait_for(asyncio.shield(state.initial_shutdown_task), 2)
            assert len(stops) == 2 and len(set(stops)) == 2
            assert (
                state.child_is_quarantined and native.runs.child_assignment(reference) is not None
            )
            assert InboxStore(str(path)).runs.cleanup_evidence_uncertain(reference)
        finally:
            # A failed cleanup stays quarantined. Teardown only releases this
            # test process and must not schedule a second retirement attempt.
            state.child = None
            await harness.runtime.shutdown()

    asyncio.run(exercise())
