"""Native receipts join retained lifecycle and real runtime publication atomically.

Separate branch tests cannot prove this combined boundary. Reuse the HTTP/core
harness and production Signal composition. No browser, native host, or provider runs.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import AsyncExitStack
from pathlib import Path
from typing import cast

from tests.unit.test_conversation_api import _Harness, _start

from planner.conversation.backend_event_contracts import (
    CommitParticipant,
)
from planner.conversation.lifecycle import LifecycleStatus
from planner.conversation.message_content import text_message_content
from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.conversation.system import SqliteProcessConversationSystem
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    AcceptanceReceipt,
    BackendEvent,
    BackendEventMethod,
    EnvelopeKind,
    JsonObject,
    QueuedEnvelope,
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
        native = InboxStore(str(path))
        native.runs.register_child(child, "chief", "chief", 1)
        await source.runtime.shutdown()
        async with AsyncExitStack() as cleanup:
            composition = SignalRuntimeComposition(str(path), 1000)
            cleanup.callback(composition.close)
            composition.bootstrap()
            from tests.support.executor_turns import MacRemoteChildFactory

            factory = MacRemoteChildFactory(
                lambda start, sink: child,
                no_native_dispatch,
                lambda start: canonical_snapshot(),
                db_path=str(path),
            )
            restored = _Harness(
                path, signal_composition=composition, cleanup=cleanup, backend_child_factory=factory
            )
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


def test_refused_evidence_before_stop_commit_keeps_core_available(
    tmp_db: sqlite3.Connection,
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

            guarded = asyncio.Event()

            async def stop_result(command: ChildCommand) -> CommandOutcome:
                if command.operation is ChildOperation.stop:
                    stops.append(command.command_id)
                if not guarded.is_set():
                    event = BackendEvent(reference, BackendEventMethod.turn_ended, token, {})
                    queued = _envelope(1, EnvelopeKind.backend_event, event)

                    def refuse(
                        connection: sqlite3.Connection, envelope: QueuedEnvelope
                    ) -> JsonObject:
                        raise PlannerError(ErrorCode.validation, "required final event refused", {})

                    receipt = native.accept(queued, refuse, 100, identity=identity)
                    assert receipt.disposition is ReceiptDisposition.refused
                    guarded.set()
                return CommandOutcome(
                    command.command_id, reference, CommandDisposition.succeeded, {}
                )

            from tests.support.executor_turns import MacRemoteChildFactory

            factory = MacRemoteChildFactory(
                lambda start, sink: reference,
                stop_result,
                lambda start: canonical_snapshot(),
                db_path=str(path),
            )
            harness.system._backend_child_factories = {
                key: factory for key in harness.system._backend_child_factories
            }
            state.child = factory.recover(reference)
            state.executor_child_reference = reference
            await asyncio.wait_for(harness.system.kill("c"), 2)
            assert guarded.is_set()
            assert not state.lock.locked()
            assert state.child is not None and state.child_is_quarantined
            assert state.executor_child_reference == reference
            assert native.runs.child_assignment(reference) is not None
            assert tmp_db.execute("SELECT stop_state FROM mac_runs").fetchone()[0] == "unproved"
            assert harness.store.recording_failures("c") == ()
            await asyncio.wait_for(harness.system.kill("c"), 2)
            assert state.initial_shutdown_task is not None
            await asyncio.wait_for(asyncio.shield(state.initial_shutdown_task), 2)
            assert len(stops) == 2 and len(set(stops)) == 2
            assert (
                state.child_is_quarantined and native.runs.child_assignment(reference) is not None
            )
            assert InboxStore(str(path)).runs.retained_runs()[0].retirement_confirmed is False
        finally:
            # A failed cleanup stays quarantined. Teardown only releases this
            # test process and must not schedule a second retirement attempt.
            state.child = None
            await harness.runtime.shutdown()

    asyncio.run(exercise())
