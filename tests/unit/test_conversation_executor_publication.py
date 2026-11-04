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
from planner.execution_identity import ChildReference
from planner.executor.protocol import (
    AcceptanceReceipt,
    BackendEvent,
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
