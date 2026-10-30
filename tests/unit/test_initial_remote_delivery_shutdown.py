"""Shutdown owns a reserved delivery without claiming native effects disappeared."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import TypeAdapter
from tests.support.executor_control import message_link
from tests.support.executor_turns import MacRemoteChildFactory
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliverySuppressed,
    PromptDeliveryUncertain,
)
from planner.conversation.events import PromptDiscardedEventPayload
from planner.conversation.lifecycle import LifecycleKind, LifecycleStatus
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore, StoredConversationEvent
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import RealClock
from planner.execution_identity import ChildReference
from planner.executor.protocol import (
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    WireMessage,
    WireMessageKind,
    decode_command_arguments,
)
from planner.executor.vps_transport import VpsExecutor


@pytest.mark.parametrize("boundary", ["prompt_frame"])
def test_initial_kill_settles_callers_and_preserves_exact_dispatch_boundary(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    """Gap: start is not prompt delivery; partial frame writes cannot prove suppression."""
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    async def run() -> None:
        endpoint = VpsExecutor(db_path, RealClock())
        endpoint.runs.ready = True
        from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
        from planner.execution_identity import ExecutorIdentity
        from planner.executor.protocol import (
            EnvelopeKind,
            JsonObject,
            QueuedEnvelope,
            encode_value,
            envelope_payload_digest,
        )

        identity = ExecutorIdentity(
            PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "original-incarnation", 1
        )
        endpoint.runs.link = message_link(
            SimpleNamespace(close=lambda: None, send=None, is_closing=lambda: False), identity
        )
        endpoint.inbox.store.runs.begin_connection(identity, 1)
        reference = ChildReference("original-incarnation", "original-child", "c_cancel")
        endpoint.inbox.store.runs.register_child(reference, "ticket", "test-ticket", 1)
        entered, release, stopped, stopped_again = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        emitted: list[ChildCommand] = []

        async def frame(message: WireMessage) -> None:
            assert endpoint.runs.link is not None
            if message.kind is WireMessageKind.withdraw:
                endpoint.runs.link._pending[message.request_id].set_result(
                    WireMessage(
                        WireMessageKind.withdraw_result, message.request_id, {"withdrawn": []}
                    )
                )
                return
            command = TypeAdapter(ChildCommand).validate_python(message.payload)
            emitted.append(command)
            if command.operation is ChildOperation.write_prompt and boundary == "prompt_frame":
                entered.set()
                await release.wait()
            if command.operation is ChildOperation.stop:
                # Native termination is deliberately unresolved while kill returns.
                stopped.set()
                if len([c for c in emitted if c.operation is ChildOperation.stop]) == 2:
                    stopped_again.set()
            elif command.operation is ChildOperation.start:
                await endpoint.runs._resolve_command(
                    CommandOutcome(
                        command.command_id, command.child, CommandDisposition.succeeded, {}
                    )
                )

        monkeypatch.setattr(endpoint.runs.link._writer, "send", frame)
        factory = MacRemoteChildFactory(
            lambda start, sink: reference,
            endpoint.runs.send_command,
            lambda start: canonical_snapshot(),
            endpoint.runs.cancel_initial_delivery,
            db_path=db_path,
        )
        system = SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(db_path),
        )
        try:
            await system.start_conversation(
                ConversationStartRequest("c_cancel", "scripted", workspace_folder=tmp_path)
            )
            original = asyncio.create_task(
                system.send(
                    "c_cancel",
                    text_message_content("Exact sender bytes Ω\n"),
                    sender_label="Owner",
                    sender_message_id="original-message",
                )
            )
            await asyncio.wait_for(entered.wait(), 2)
            duplicate = asyncio.create_task(
                system.send(
                    "c_cancel",
                    text_message_content("Exact sender bytes Ω\n"),
                    sender_label="Owner",
                    sender_message_id="original-message",
                )
            )
            await asyncio.wait_for(system.kill("c_cancel"), 2)
            first, repeated = await asyncio.wait_for(asyncio.gather(original, duplicate), 2)
            expected = (
                PromptDeliveryUncertain if boundary == "prompt_frame" else PromptDeliverySuppressed
            )
            assert isinstance(first, expected) and isinstance(repeated, expected)
            await asyncio.wait_for(stopped.wait(), 2)
            state = system._conversations["c_cancel"]
            assert state.reserved_turn is None and state.running_turn is None
            assert state.child_is_quarantined and state.child is not None
            assert state.executor_child_reference == reference
            assert endpoint.inbox.store.runs.child_assignment(reference) is not None
            release.set()
            for command in emitted:
                if command.operation is ChildOperation.write_prompt:
                    assert boundary == "prompt_frame"
            facts = await system._store.lifecycle_facts("c_cancel")
            assert any(
                f.kind is LifecycleKind.turn and f.status is LifecycleStatus.unavailable
                for f in facts
            )
            assert any(
                f.status
                is (
                    LifecycleStatus.unknown
                    if boundary == "prompt_frame"
                    else LifecycleStatus.suppressed
                )
                for f in facts
            )
            assert (
                tmp_db.execute("SELECT count(*) FROM executor_command_outcomes").fetchone()[0] == 0
            )

            # A late native result is evidence only; local reservation never reopens.
            prompt = next((c for c in emitted if c.operation is ChildOperation.write_prompt), None)
            if prompt is not None:
                await endpoint.runs._resolve_command(
                    CommandOutcome(prompt.command_id, reference, CommandDisposition.succeeded, {})
                )
                assert state.running_turn is None and state.reserved_turn is None

            # Restart before confirmed stop preserves a settled token plus quarantine.
            await system.shutdown()
            restored = SqliteProcessConversationSystem(
                store=ConversationStore(db_path),
                backend_child_factories={key: factory for key in ConversationBackendKey},
                message_files=ConversationMessageFiles(db_path),
            )
            try:
                recovered = restored._conversations["c_cancel"]
                assert recovered.reserved_turn is None and recovered.child_is_quarantined
                assert recovered.executor_child_reference == reference
                await asyncio.wait_for(restored.kill("c_cancel"), 2)
                # Wait until the new owned stop has its exact persisted command ID.
                await asyncio.wait_for(stopped_again.wait(), 2)
                stops = [c for c in emitted if c.operation is ChildOperation.stop]
                assert len(stops) == 2
                final_stop = stops[-1]
                outcome = CommandOutcome(
                    final_stop.command_id, reference, CommandDisposition.succeeded, {}
                )
                # A direct result alone cannot prove the original FIFO receipt.
                await endpoint.runs._resolve_command(outcome)
                assert recovered.initial_shutdown_task is not None
                assert not recovered.initial_shutdown_task.done()
                payload = cast(JsonObject, encode_value(outcome))
                submission = "stopped_" + hashlib.sha256(final_stop.command_id.encode()).hexdigest()
                digest = envelope_payload_digest(
                    payload,
                    (),
                    sender_id="native",
                    conversation_id="c_cancel",
                    sequence=1,
                    submission_id=submission,
                    kind=EnvelopeKind.child_stopped,
                )
                await endpoint.inbox.accept(
                    QueuedEnvelope(
                        "native",
                        "c_cancel",
                        1,
                        submission,
                        EnvelopeKind.child_stopped,
                        payload,
                        digest,
                    ),
                    identity,
                )
                assert recovered.initial_shutdown_task is not None
                await asyncio.wait_for(recovered.initial_shutdown_task, 2)
                assert not recovered.child_is_quarantined
                assert endpoint.inbox.store.runs.child_assignment(reference) is None
                assert endpoint.inbox.store.runs.retained_runs()[0].retirement_confirmed
            finally:
                await restored.shutdown()
        finally:
            release.set()
            await system.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("fail_disposition", [False, True])
def test_preparation_kill_settles_sender_and_old_continuation_cannot_run_after_successor(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_disposition: bool
) -> None:
    """Gap: no child exists yet, and the ended token may advance before old work resumes."""
    from dataclasses import replace

    from planner.conversation.contracts import PromptDeliveryStarted

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    commands: list[ChildCommand] = []

    async def run() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(command: ChildCommand) -> CommandOutcome:
            commands.append(command)
            return CommandOutcome(
                command.command_id,
                command.child,
                CommandDisposition.succeeded,
                {"composed_content_delivered": True},
            )

        factory = MacRemoteChildFactory(
            lambda start, sink: ChildReference("incarnation", "successor", "c_preparation"),
            send,
            lambda start: canonical_snapshot(),
            db_path=db_path,
        )
        system = SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(db_path),
        )
        try:
            await system.start_conversation(
                ConversationStartRequest("c_preparation", "scripted", workspace_folder=tmp_path)
            )
            original_ensure = system._ensure_child
            blocked = True

            async def ensure(*args: Any, **kwargs: Any) -> Any:
                nonlocal blocked
                if blocked:
                    blocked = False
                    entered.set()
                    await release.wait()
                return await original_ensure(*args, **kwargs)

            monkeypatch.setattr(system, "_ensure_child", ensure)
            first = asyncio.create_task(
                system.send(
                    "c_preparation",
                    text_message_content("old"),
                    sender_label="Owner",
                    sender_message_id="old-preparation",
                )
            )
            await asyncio.wait_for(entered.wait(), 2)
            if fail_disposition:
                state = system._conversations["c_preparation"]
                reservation = state.reserved_turn
                assert reservation is not None
                original_message = reservation.messages[0]
                reservation.messages += tuple(
                    replace(
                        original_message,
                        content=text_message_content(text),
                        sender_message_id=None,
                    )
                    for text in ("second without ID", "third without ID")
                )
                append = system._store.append_in_transaction
                persist = system._persist
                failed = False

                def fail_after_batch(
                    *args: Any, **kwargs: Any
                ) -> tuple[StoredConversationEvent, ...]:
                    nonlocal failed
                    written = append(*args, **kwargs)
                    if not failed:
                        failed = True
                        raise RuntimeError("injected disposition failure")
                    return written

                async def expose_first_failure(state: Any, operation: Any, **kwargs: Any) -> Any:
                    if (
                        operation.func == system._store.append_initial_shutdown_disposition
                        and not failed
                    ):
                        # Expose this injected transaction failure instead of the ordinary
                        # recording pause, so a subsequent kill exercises owner retry.
                        return await operation()
                    return await persist(state, operation, **kwargs)

                monkeypatch.setattr(system._store, "append_in_transaction", fail_after_batch)
                monkeypatch.setattr(system, "_persist", expose_first_failure)
                with pytest.raises(RuntimeError, match="injected disposition failure"):
                    await asyncio.wait_for(system.kill("c_preparation"), 2)
                assert not first.done()
                assert not any(
                    event.kind.value == "prompt_discarded"
                    for event in await system._store.read_events_after("c_preparation", 0)
                )
                assert reservation.shutdown_owned and reservation.shutdown_fate is None
            await asyncio.wait_for(system.kill("c_preparation"), 2)
            if fail_disposition:
                discarded = [
                    event.payload.content
                    for event in await system._store.read_events_after("c_preparation", 0)
                    if isinstance(event.payload, PromptDiscardedEventPayload)
                ]
                assert discarded == [
                    text_message_content(text)
                    for text in ("old", "second without ID", "third without ID")
                ]
            assert isinstance(await asyncio.wait_for(first, 2), PromptDeliverySuppressed)
            assert commands == []
            successor = await system.send(
                "c_preparation", text_message_content("new"), sender_label="Owner"
            )
            assert isinstance(successor, PromptDeliveryStarted)
            await system.interrupt("c_preparation")
            before = list(commands)
            release.set()
            await system.wait_until_quiescent()
            assert commands == before
            assert not await system.is_running("c_preparation")
        finally:
            release.set()
            await system.shutdown()

    asyncio.run(run())


def test_closed_assignment_admits_exact_late_native_result_without_renewing_authority(
    tmp_db: Connection,
) -> None:
    """Gap: a real late result must persist without admitting unknown command IDs."""
    from dataclasses import replace

    from tests.unit.test_executor_authoritative_store import IDENTITY

    from planner.conversation.backends.contracts import TurnToken
    from planner.core.errors import PlannerError
    from planner.executor.protocol import (
        EnvelopeKind,
        JsonObject,
        QueuedEnvelope,
        ReceiptDisposition,
        encode_value,
        envelope_payload_digest,
    )

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    endpoint = VpsExecutor(db_path, RealClock())
    endpoint.inbox.store.runs.begin_connection(IDENTITY, 1)
    reference = ChildReference(IDENTITY.executor_incarnation, "closed-child", "closed-conversation")
    endpoint.inbox.store.runs.register_child(reference, "ticket", "closed-ticket", 1)
    command = ChildCommand(
        "original-stop-command",
        reference,
        ChildOperation.stop,
        TurnToken(reference.conversation_id, 1),
        decode_command_arguments(ChildOperation.stop, {}),
    )
    tmp_db.commit()
    endpoint.inbox.store.runs.close_child(reference, 2)

    def envelope(outcome: CommandOutcome, *, sequence: int) -> QueuedEnvelope:
        payload = cast(JsonObject, encode_value(outcome))
        submission_id = f"native-result-{sequence}"
        digest = envelope_payload_digest(
            payload,
            (),
            sender_id="native",
            conversation_id=reference.conversation_id,
            sequence=sequence,
            submission_id=submission_id,
            kind=EnvelopeKind.child_stopped,
        )
        return QueuedEnvelope(
            "native",
            reference.conversation_id,
            sequence,
            submission_id,
            EnvelopeKind.child_stopped,
            payload,
            digest,
        )

    async def run() -> None:
        endpoint.runs.pending_commands[command.command_id] = (
            command,
            asyncio.get_running_loop().create_future(),
        )
        actual = CommandOutcome(command.command_id, reference, CommandDisposition.succeeded, {})
        receipt = await endpoint.inbox.accept(envelope(actual, sequence=1), IDENTITY)
        assert receipt.disposition is ReceiptDisposition.accepted
        assert tmp_db.execute("SELECT count(*) FROM executor_command_outcomes").fetchone()[0] == 1
        assert endpoint.inbox.store.runs.child_assignment(reference) is None
        with pytest.raises(PlannerError, match="assignment"):
            endpoint.runs.authority(reference)
        refused = await endpoint.inbox.accept(
            envelope(replace(actual, command_id="invented-command"), sequence=2), IDENTITY
        )
        assert refused.disposition is ReceiptDisposition.refused
        # The first native result cannot be rewritten, even through historical admission.
        conflict = await endpoint.inbox.accept(
            envelope(replace(actual, disposition=CommandDisposition.uncertain), sequence=3),
            IDENTITY,
        )
        assert conflict.disposition is ReceiptDisposition.refused
        assert "successful exact-child stop" in str(conflict.result)
        stored = json.loads(
            tmp_db.execute("SELECT outcome_json FROM executor_command_outcomes").fetchone()[0]
        )
        assert stored["disposition"] == "succeeded"

    asyncio.run(run())


def test_retirement_rolls_back_run_and_assignment_together(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: a crash after stop must not retain authority beside retired metadata."""
    from planner.executor.store import ExecutorStore
    from planner.executor.vps_inbox import InboxStore

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    reference = ChildReference("retired-incarnation", "retired-child", "c_atomic_retirement")
    executors = InboxStore(db_path)
    executors.runs.register_child(reference, "ticket", "retirement-ticket", 1)
    close = ExecutorStore.close_child_in_transaction

    def fail_after_close(
        connection: Connection,
        child: ChildReference,
        now_ms: int,
    ) -> None:
        close(connection, child, now_ms)
        raise RuntimeError("injected retirement failure")

    with monkeypatch.context() as patch:
        patch.setattr(ExecutorStore, "close_child_in_transaction", staticmethod(fail_after_close))
        with pytest.raises(RuntimeError, match="injected retirement failure"):
            executors.runs.close_child(reference, 2)
    assert not executors.runs.retained_runs()[0].retirement_confirmed
    assert executors.runs.child_assignment(reference) == ("ticket", "retirement-ticket")
    executors.runs.close_child(reference, 3)
    assert executors.runs.retained_runs()[0].retirement_confirmed
    assert executors.runs.child_assignment(reference) is None
