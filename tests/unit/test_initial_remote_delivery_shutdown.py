"""Shutdown owns a reserved delivery without claiming native effects disappeared."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection, IntegrityError
from typing import Any, cast

import pytest
from tests.support.executor_presence import mac_identity, make_current
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
    identity = mac_identity()
    make_current(db_path, identity)

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
            lambda start, sink: ChildReference(
                identity.executor_incarnation, "successor", "c_preparation"
            ),
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
                    if operation.func == system._store.append_events and not failed:
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

    from planner.conversation.backends.contracts import TurnToken
    from planner.core.errors import PlannerError
    from planner.executor.protocol import (
        EnvelopeKind,
        JsonObject,
        QueuedEnvelope,
        ReceiptDisposition,
        encode_value,
    )

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    identity = mac_identity()
    make_current(db_path, identity)
    endpoint = VpsExecutor(db_path, RealClock())
    reference = ChildReference(identity.executor_incarnation, "closed-child", "closed-conversation")
    endpoint.inbox.store.runs.register_child(reference, "ticket", "closed-ticket", 1)
    command = ChildCommand(
        "original-stop-command",
        reference,
        ChildOperation.stop,
        TurnToken(reference.conversation_id, 1),
        decode_command_arguments(ChildOperation.stop, {}),
    )
    tmp_db.commit()

    def envelope(outcome: CommandOutcome, *, sequence: int) -> QueuedEnvelope:
        payload = cast(JsonObject, encode_value(outcome))
        submission_id = f"native-result-{sequence}"
        return QueuedEnvelope(
            "native",
            reference.conversation_id,
            sequence,
            submission_id,
            EnvelopeKind.child_stopped,
            payload,
        )

    async def run() -> None:
        stopping = asyncio.create_task(endpoint.runs.stop(command))
        for _ in range(200):
            if endpoint.mailbox.has_waiter(command.command_id):
                break
            await asyncio.sleep(0.01)
        assert endpoint.mailbox.has_waiter(command.command_id)
        # The assignment closes while the stop's proof is still on its way.
        endpoint.inbox.store.runs.close_child(reference, 2)
        actual = CommandOutcome(command.command_id, reference, CommandDisposition.succeeded, {})
        receipt = await endpoint.inbox.accept(envelope(actual, sequence=1), identity)
        assert receipt.disposition is ReceiptDisposition.accepted
        assert receipt.result == {
            "recorded": True,
            "stop_command_id": command.command_id,
            "stopped_child": encode_value(reference),
        }
        assert await asyncio.wait_for(stopping, 2) == actual
        assert endpoint.inbox.store.runs.child_assignment(reference) is None
        with pytest.raises(PlannerError, match="assignment"):
            endpoint.runs.authority(reference)
        refused = await endpoint.inbox.accept(
            envelope(replace(actual, command_id="invented-command"), sequence=2), identity
        )
        assert refused.disposition is ReceiptDisposition.refused
        # Completion does not renew authority for another publication.
        conflict = await endpoint.inbox.accept(
            envelope(replace(actual, disposition=CommandDisposition.uncertain), sequence=3),
            identity,
        )
        assert conflict.disposition is ReceiptDisposition.refused
        await endpoint.close()

    asyncio.run(run())


def test_retirement_rolls_back_run_and_assignment_together(tmp_db: Connection) -> None:
    """Gap: a crash after stop must not retain authority beside retired metadata."""
    from planner.executor.vps_inbox import InboxStore

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    identity = mac_identity()
    make_current(db_path, identity)
    reference = ChildReference(
        identity.executor_incarnation, "retired-child", "c_atomic_retirement"
    )
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


@pytest.mark.parametrize(
    "landing",
    ["held", "after_withdrawal"],
)
def test_kill_withdraws_a_stalled_initial_input_without_a_round_trip(
    tmp_db: Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, landing: str
) -> None:
    """Risk: a kill during a Mac child's initial delivery records the prompt as never sent
    while its row can still reach the Mac. Gap: the mailbox test withdraws committed rows
    under a bare child; this drives the real core's kill over the real MacRuns while the
    prompt's put is still on its way. The Mac never polls: the proof is the mailbox."""
    import threading

    import planner.executor.vps_mailbox as vps_mailbox
    from planner.conversation.contracts import ConversationRoleMaterials
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.tickets import data

    ticket = data.create_ticket(
        tmp_db,
        title="Killed during its first delivery",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="scripted",
    )
    tmp_db.commit()
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    identity = mac_identity()
    make_current(db_path, identity)

    async def run() -> None:
        endpoint = VpsExecutor(db_path, RealClock())
        system = SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            backend_child_factories={key: endpoint.runs for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(db_path),
        )
        endpoint.bind_conversations(system)
        entered, kill_started = threading.Event(), threading.Event()
        kill_returned, withdrawal_looked, inserted = (
            threading.Event(),
            threading.Event(),
            threading.Event(),
        )
        original = vps_mailbox.SqliteCommandMailbox._insert_child_command

        def held_prompt(self: Any, command: ChildCommand, *args: Any) -> Any:
            if command.operation is not ChildOperation.write_prompt:
                return original(self, command, *args)
            entered.set()
            assert kill_started.wait(5)
            # The prompt's write lands after the kill returned, or right after the
            # withdrawal looked for it. A withdrawal that first waits for the write
            # in flight gets it after this bound instead.
            (kill_returned if landing == "held" else withdrawal_looked).wait(1)
            try:
                return original(self, command, *args)
            finally:
                inserted.set()

        monkeypatch.setattr(vps_mailbox.SqliteCommandMailbox, "_insert_child_command", held_prompt)
        cancel = endpoint.runs.cancel_initial_delivery

        async def withdraw_then_land(*args: Any) -> bool:
            may_have_sent = await cancel(*args)
            if landing == "after_withdrawal":
                withdrawal_looked.set()
                assert await asyncio.to_thread(inserted.wait, 5)
            return may_have_sent

        monkeypatch.setattr(endpoint.runs, "cancel_initial_delivery", withdraw_then_land)
        try:
            await system.start_conversation(
                ConversationStartRequest(
                    "c_killed",
                    "scripted",
                    workspace_folder=tmp_path,
                    role_materials=ConversationRoleMaterials(
                        "Worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id))
                    ),
                )
            )
            sending = asyncio.create_task(
                system.send("c_killed", text_message_content("old"), sender_label="Owner")
            )
            assert await asyncio.to_thread(entered.wait, 5)
            kill_started.set()
            await asyncio.wait_for(system.kill("c_killed"), 5)
            kill_returned.set()
            assert await asyncio.to_thread(inserted.wait, 5)
            result = await asyncio.wait_for(sending, 5)
            assert isinstance(result, PromptDeliverySuppressed)
            assert any(
                isinstance(event.payload, PromptDiscardedEventPayload)
                for event in await system._store.read_events_after("c_killed", 0)
            )
            # Recorded as never sent: the Mac must get nothing that could send it, and
            # the start was withdrawn on the VPS without asking the Mac.
            polled = await endpoint.poll(identity, 0, 0)
            assert [command.operation.value for command in polled.commands] == ["stop"]
            start = tmp_db.execute(
                "SELECT state,json_extract(outcome_json,'$.child_outcome.result.reason') "
                "FROM executor_commands WHERE operation='start'"
            ).fetchone()
            assert tuple(start) == ("withdrawn", "delivery_withdrawn")
        finally:
            kill_started.set()
            kill_returned.set()
            await system.shutdown()
            await endpoint.close()

    asyncio.run(run())
