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

from planner.conversation.backends.contracts import BackendPermissionAsk, BackendUserInputRequest
from planner.conversation.contracts import SignalBatch
from planner.conversation.events import PermissionAskOption, UserInputAnswer, UserInputQuestion
from planner.conversation.lifecycle import LifecycleStatus, LifecycleTransition
from planner.conversation.message_content import text_message_content
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.executor.codec import encode_value
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AnswerApplication,
    AskKind,
    AskReference,
    BackendEvent,
    BackendEventMethod,
    ChildReference,
    EnvelopeKind,
    ExecutorIdentity,
    JsonObject,
    QueuedEnvelope,
)
from planner.executor.journal import envelope_payload_digest
from planner.executor.store import ExecutorStore


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
            store = ExecutorStore(str(harness.db_path))
            identity = ExecutorIdentity(
                PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "native-process", 1
            )
            store.begin_connection(identity, 100)
            store.register_child(child, "chief", "chief", 100)
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

            def accept(row: QueuedEnvelope, writer: Any, **publication: Any) -> Any:
                def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
                    conn = connection
                    result = writer(conn, envelope)
                    if kind is AskKind.permission:
                        store.publish_ask_in_transaction(conn, ask, ("allow",), 101)
                    else:
                        store.publish_user_input_request_in_transaction(conn, ask, questions, 101)
                    return cast(JsonObject, result)

                return store.accept(row, effect, 101, identity=identity, **publication)

            def fail(conn: sqlite3.Connection, candidate: LifecycleTransition) -> None:
                assert conn.in_transaction
                assert any(f.provider_identity == ask.ask_id for f in candidate.facts)
                raise RuntimeError("mixed participant refused")

            harness.store._transaction_participant = fail
            with pytest.raises(RuntimeError, match="mixed participant refused"):
                await harness.system.receive_remote_backend_event(event, envelope, accept)
            assert store.lookup_receipt("native", "native-1") is None
            assert not await harness.system.has_pending_permission_ask("c")
            assert not await harness.system.has_pending_user_input("c")
            harness.store._transaction_participant = None
            receipt = await harness.system.receive_remote_backend_event(event, envelope, accept)
            assert (
                await harness.system.receive_remote_backend_event(event, envelope, accept)
                == receipt
            )
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
            context = store.turn_context("c")
            assert context is not None
            assert ask.ask_id in (
                context.pending_permission_ask_ids
                if kind is AskKind.permission
                else context.pending_user_input_ids
            )
            store.mark_lost(identity, 102)
            choice = (
                store.choose_answer(ask, "allow", 1, 103)
                if kind is AskKind.permission
                else store.choose_user_input_answer(
                    ask, (UserInputAnswer("q", ("answer",)),), 1, 103
                )
            )
            evidence = store.ask_evidence(ask)
            assert evidence is not None and evidence.applied_at_mac_ms is None
            application = AnswerApplication(choice.answer_id, ask, 104)
            applied_envelope = _envelope(2, EnvelopeKind.answer_application, application)

            def accept_application(row: QueuedEnvelope, writer: Any, **publication: Any) -> Any:
                return store.accept(row, writer, 105, identity=identity, **publication)

            # Reconnect the same process with a fresh generation before native evidence arrives.
            identity = ExecutorIdentity(
                PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "native-process", 2
            )
            store.begin_connection(identity, 104)
            applied = await harness.system.receive_remote_answer_application(
                application,
                applied_envelope,
                accept_application,
                store.record_application_in_transaction,
            )
            assert (
                await harness.system.receive_remote_answer_application(
                    application,
                    applied_envelope,
                    accept_application,
                    store.record_application_in_transaction,
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


def test_restart_restores_native_request_before_initial_signal_snapshot(
    tmp_db: sqlite3.Connection,
) -> None:
    """A VPS restart cannot erase offline answerability or manufacture local recovery."""
    from tests.unit.test_executor_native_runtime import canonical_snapshot

    from planner.conversation.contracts import SignalSnapshot
    from planner.executor.contracts import ChildCommand, CommandOutcome
    from planner.executor.remote import RemoteBackendChild

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

        state.child = RemoteBackendChild(
            child, no_native_dispatch, lambda start: canonical_snapshot()
        )
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
            assert next(
                v for v in snapshots[0].values if v.conversation_id == "c"
            ).attention.awaiting_answer
            assert await restored.system.pending_permission_ask_ids("c") == frozenset({public_id})
            assert any(
                f.identity == public_id and f.status is LifecycleStatus.pending
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
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        CommandDisposition,
        CommandOutcome,
        ReceiptDisposition,
    )
    from planner.executor.remote import RemoteBackendChild

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
            native = ExecutorStore(str(path))
            identity = ExecutorIdentity(
                PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, reference.executor_incarnation, 1
            )
            native.begin_connection(identity, 1)
            native.register_child(reference, "chief", "chief", 1)

            stops: list[str] = []

            async def stop_result(command: ChildCommand) -> CommandOutcome:
                if command.operation is ChildOperation.stop:
                    stops.append(command.command_id)
                return CommandOutcome(
                    command.command_id, reference, CommandDisposition.succeeded, {}
                )

            state.child = RemoteBackendChild(
                reference, stop_result, lambda start: canonical_snapshot()
            )
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
                    assert not native.cleanup_evidence_uncertain(reference)
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
            assert native.child_assignment(reference) is not None
            assert native.cleanup_evidence_uncertain(reference)
            assert harness.store.recording_failures("c") == ()
            await asyncio.wait_for(harness.system.kill("c"), 2)
            assert len(stops) == 2 and len(set(stops)) == 2
            assert state.child_is_quarantined and native.child_assignment(reference) is not None
            assert ExecutorStore(str(path)).cleanup_evidence_uncertain(reference)
        finally:
            # A failed cleanup stays quarantined. Teardown only releases this
            # test process and must not schedule a second retirement attempt.
            state.child = None
            await harness.runtime.shutdown()

    asyncio.run(exercise())
