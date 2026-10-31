"""Shutdown owns a reserved delivery without claiming native effects disappeared."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection, IntegrityError
from typing import Any, cast

import pytest
from tests.support.executor_turns import MacRemoteChildFactory
from tests.unit.test_executor_native_runtime import canonical_snapshot

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliverySuppressed,
)
from planner.conversation.events import PromptDiscardedEventPayload
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
    decode_command_arguments,
)
from planner.executor.vps_transport import VpsExecutor


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
                        operation.func == system._store.append_events
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
    endpoint.inbox.store.runs.begin_connection(IDENTITY)
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
        stopping = asyncio.create_task(endpoint.runs.send_command(command))
        await asyncio.sleep(0)
        actual = CommandOutcome(command.command_id, reference, CommandDisposition.succeeded, {})
        receipt = await endpoint.inbox.accept(envelope(actual, sequence=1), IDENTITY)
        assert receipt.disposition is ReceiptDisposition.accepted
        assert receipt.result == {"recorded": True}
        assert await asyncio.wait_for(stopping, 2) == actual
        assert endpoint.inbox.store.runs.child_assignment(reference) is None
        with pytest.raises(PlannerError, match="assignment"):
            endpoint.runs.authority(reference)
        refused = await endpoint.inbox.accept(
            envelope(replace(actual, command_id="invented-command"), sequence=2), IDENTITY
        )
        assert refused.disposition is ReceiptDisposition.refused
        # Completion does not renew authority for another publication.
        conflict = await endpoint.inbox.accept(
            envelope(replace(actual, disposition=CommandDisposition.uncertain), sequence=3),
            IDENTITY,
        )
        assert conflict.disposition is ReceiptDisposition.refused

    asyncio.run(run())


def test_retirement_rolls_back_run_and_assignment_together(tmp_db: Connection) -> None:
    """Gap: a crash after stop must not retain authority beside retired metadata."""
    from planner.executor.vps_inbox import InboxStore

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    reference = ChildReference("retired-incarnation", "retired-child", "c_atomic_retirement")
    executors = InboxStore(db_path)
    executors.runs.register_child(reference, "ticket", "retirement-ticket", 1)
    tmp_db.execute(
        "CREATE TRIGGER refuse_retirement BEFORE UPDATE ON executor_child_assignments "
        "BEGIN SELECT RAISE(ABORT,'injected retirement failure'); END"
    )
    tmp_db.commit()
    with pytest.raises(IntegrityError, match="injected retirement failure"):
        executors.runs.close_child(reference, 2)
    assert not executors.runs.retained_runs()[0].retirement_confirmed
    assert executors.runs.child_assignment(reference) == ("ticket", "retirement-ticket")
    tmp_db.execute("DROP TRIGGER refuse_retirement")
    tmp_db.commit()
    executors.runs.close_child(reference, 3)
    assert executors.runs.retained_runs()[0].retirement_confirmed
    assert executors.runs.child_assignment(reference) is None
