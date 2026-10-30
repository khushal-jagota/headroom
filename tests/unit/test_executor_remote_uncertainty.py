"""An unproved native prompt cannot turn into refusal or an automatic retry."""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.conversation.backends.contracts import BackendChild, RemoteCommandUncertain
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryUncertain,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.execution_identity import ChildReference
from planner.executor.protocol import (
    CanonicalSkillSnapshot,
    CanonicalSkillSource,
    ChildCommand,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    StartRun,
    encode_value,
)
from planner.executor.vps_remote_child import MacRemoteChild, MacRemoteChildFactory


def test_unknown_prompt_is_durable_and_quarantined_across_vps_restart(
    tmp_db: Connection, tmp_path: Path
) -> None:
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    commands: list[ChildCommand] = []

    async def native(command: ChildCommand) -> CommandOutcome:
        commands.append(command)
        disposition = (
            CommandDisposition.succeeded
            if command.operation is ChildOperation.start
            else CommandDisposition.uncertain
        )
        return CommandOutcome(command.command_id, command.child, disposition, {})

    factory = MacRemoteChildFactory(
        lambda start, sink: ChildReference("incarnation", "child", start.conversation_id),
        native,
        lambda start: CanonicalSkillSnapshot(
            tuple(
                CanonicalSkillSource(name, f"---\nname: {name}\ndescription: test\n---\nTest\n")
                for name in ("panels", "panels-worker", "panels-worker-coding")
            ),
            ("panels", "panels-worker", "panels-worker-coding"),
        ),
    )

    def core() -> SqliteProcessConversationSystem:
        return SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(db_path),
        )

    async def run() -> None:
        system = core()
        await system.start_conversation(
            ConversationStartRequest("c_unknown", "scripted", workspace_folder=tmp_path)
        )
        result = await system.send(
            "c_unknown",
            text_message_content("Original"),
            sender_label="Test",
            sender_message_id="stable_sender",
        )
        assert isinstance(result, PromptDeliveryUncertain)
        assert system._conversations["c_unknown"].child_is_quarantined
        assert system._conversations["c_unknown"].reserved_turn is not None
        await system.shutdown()
        restored = core()
        replay = await restored.send(
            "c_unknown",
            text_message_content("Original"),
            sender_label="Test",
            sender_message_id="stable_sender",
        )
        assert isinstance(replay, PromptDeliveryUncertain)
        assert restored._conversations["c_unknown"].child_is_quarantined
        assert restored._conversations["c_unknown"].reserved_turn is None
        assert not await restored.is_running("c_unknown")
        assert [command.operation for command in commands] == [
            ChildOperation.start,
            ChildOperation.write_prompt,
        ]
        kinds = [
            row[0]
            for row in tmp_db.execute(
                "SELECT kind FROM conversation_events WHERE conversation_id='c_unknown'"
            )
        ]
        assert kinds == ["prompt_delivery_uncertain"]
        await restored.shutdown()

    asyncio.run(run())


def test_each_native_start_reads_database_snapshot_and_replay_keeps_command(
    tmp_db: Connection, tmp_path: Path
) -> None:
    from dataclasses import replace
    from typing import cast

    from pydantic import TypeAdapter

    from planner.conversation.contracts import (
        ConversationAccess,
        ConversationRoleMaterials,
        ResolvedConversationStart,
    )
    from planner.core.clock import RealClock
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.executor.configuration import MAX_ENVELOPE_BYTES
    from planner.executor.protocol import JsonObject, WireMessage, WireMessageKind, encode_message
    from planner.executor.vps_remote_child import MacRemoteChild
    from planner.executor.vps_transport import VpsExecutor
    from planner.managed_skills import read_all_skill_sources, write_skill_source
    from planner.tickets import data

    ticket = data.create_ticket(
        tmp_db,
        title="Canonical native launch",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        employee_backend="codex",
        employee_launch_model="scripted",
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    endpoint = VpsExecutor(path, RealClock())
    start = ResolvedConversationStart(
        "c_snapshot",
        ConversationBackendKey.codex,
        "scripted",
        None,
        ConversationRoleMaterials(
            "Worker", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", ticket.id))
        ),
        tmp_path,
        ConversationAccess.full,
    )
    commands: list[ChildCommand] = []

    async def send(command: ChildCommand) -> CommandOutcome:
        commands.append(command)
        return CommandOutcome(command.command_id, command.child, CommandDisposition.succeeded, {})

    async def run() -> None:
        first_text = read_all_skill_sources(tmp_db)["panels"] + "\nDatabase owner text α.\r\n"
        write_skill_source(tmp_db, "panels", first_text, now=2)
        child = MacRemoteChild(
            ChildReference("incarnation", "one", "c_snapshot"),
            send,
            endpoint.runs._read_skill_snapshot,
        )
        await child.start(start, vendor_session_cursor=None)
        first = commands[0]
        assert isinstance(first.arguments, StartRun)
        snapshot = TypeAdapter(CanonicalSkillSnapshot).validate_python(
            encode_value(first.arguments.canonical_skill_snapshot)
        )
        assert (
            next(row.source_text for row in snapshot.sources if row.skill_name == "panels")
            == first_text
        )
        frame = encode_message(
            WireMessage(
                WireMessageKind.command,
                first.command_id,
                cast(JsonObject, TypeAdapter(ChildCommand).dump_python(first, mode="json")),
            )
        )
        assert len(frame) < MAX_ENVELOPE_BYTES
        assert len(snapshot.sources) == len(read_all_skill_sources(tmp_db))
        second_text = first_text + "Later canonical rows.\n"
        write_skill_source(tmp_db, "panels", second_text, now=3)
        # A retry sends the retained command, with no supplier read or enrichment.
        await send(first)
        assert commands[1] is first
        assert isinstance(commands[1].arguments, StartRun)
        assert isinstance(first.arguments, StartRun)
        assert encode_value(commands[1].arguments.canonical_skill_snapshot) == encode_value(
            first.arguments.canonical_skill_snapshot
        )
        await child.start(replace(start), vendor_session_cursor="retained-provider-session")
        assert isinstance(commands[2].arguments, StartRun)
        resumed = TypeAdapter(CanonicalSkillSnapshot).validate_python(
            encode_value(commands[2].arguments.canonical_skill_snapshot)
        )
        assert (
            next(row.source_text for row in resumed.sources if row.skill_name == "panels")
            == second_text
        )
        assert isinstance(commands[2].arguments, StartRun)
        assert (
            encode_value(commands[2].arguments.vendor_session_cursor) == "retained-provider-session"
        )
        assert commands[2].command_id != first.command_id

    asyncio.run(run())


def test_oversized_full_start_frame_refuses_before_dispatch(tmp_path: Path) -> None:
    import pytest

    from planner.conversation.backends.contracts import BackendSpawnFailed
    from planner.conversation.contracts import ConversationAccess, ResolvedConversationStart
    from planner.executor.configuration import MAX_ENVELOPE_BYTES
    from planner.executor.vps_remote_child import MacRemoteChild

    names = ("panels", "panels-worker", "panels-worker-coding")
    # JSON escaping makes the encoded command larger than the raw skill sources.
    snapshot = CanonicalSkillSnapshot(
        tuple(
            CanonicalSkillSource(
                name,
                f"---\nname: {name}\ndescription: test\n---\n" + '"' * (MAX_ENVELOPE_BYTES // 5),
            )
            for name in names
        ),
        names,
    )
    commands: list[ChildCommand] = []

    async def send(command: ChildCommand) -> CommandOutcome:
        commands.append(command)
        return CommandOutcome(command.command_id, command.child, CommandDisposition.succeeded, {})

    async def run() -> None:
        child = MacRemoteChild(
            ChildReference("inc", "child", "c_large"), send, lambda start: snapshot
        )
        resolved = ResolvedConversationStart(
            "c_large",
            ConversationBackendKey.codex,
            "scripted",
            None,
            None,
            tmp_path,
            ConversationAccess.full,
        )
        with pytest.raises(BackendSpawnFailed, match="frame exceeds envelope limit"):
            await child.start(resolved, vendor_session_cursor=None)
        assert commands == []

    asyncio.run(run())


def test_explicit_cleanup_retry_recovers_through_locale_factory_and_fresh_turn_restores(
    tmp_db: Connection, tmp_path: Path
) -> None:
    """A failed owned stop cannot consume the next explicit cleanup or taint fresh work."""
    import hashlib
    import json
    from typing import Any, cast

    from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
    from planner.execution_identity import ExecutorIdentity
    from planner.executor.protocol import (
        EnvelopeKind,
        JsonObject,
        QueuedEnvelope,
        encode_value,
        envelope_payload_digest,
    )
    from planner.executor.routing import LocaleBackendChildFactory
    from planner.executor.vps_inbox import InboxStore

    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    store = InboxStore(db_path)
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "native", 1)
    store.runs.begin_connection(identity, 1)
    commands: list[ChildCommand] = []
    references: list[ChildReference] = []
    sequence = 0
    stops = 0
    cancel_states: list[Any] = []

    def assign(start: Any, sink: Any) -> ChildReference:
        del sink
        reference = ChildReference("native", f"child-{len(references)}", start.conversation_id)
        references.append(reference)
        store.runs.register_child(reference, "ticket", "test", 1)
        return reference

    async def native(command: ChildCommand) -> CommandOutcome:
        nonlocal sequence, stops
        commands.append(command)
        if command.operation is ChildOperation.cancel_running_turn and cancel_states:
            # A preceding native publication needs the core lock before the cancel
            # outcome can settle. Remote kill must leave that lock available.
            async with cancel_states[-1].lock:
                assert cancel_states[-1].running_turn.ending_is_the_cores
        if command.operation is ChildOperation.stop:
            stops += 1
        disposition = (
            CommandDisposition.uncertain
            if (command.operation is ChildOperation.write_prompt and len(references) == 1)
            or (command.operation is ChildOperation.stop and stops == 1)
            else CommandDisposition.succeeded
        )
        outcome = CommandOutcome(
            command.command_id,
            command.child,
            disposition,
            {"composed_content_delivered": True}
            if command.operation is ChildOperation.write_prompt
            else {},
        )
        if (
            command.operation is not ChildOperation.stop
            or disposition is not CommandDisposition.succeeded
        ):
            return outcome
        sequence += 1
        payload = cast(JsonObject, encode_value(outcome))
        submission = "stopped_" + hashlib.sha256(command.command_id.encode()).hexdigest()
        digest = envelope_payload_digest(
            payload,
            (),
            sender_id="native",
            conversation_id=command.child.conversation_id,
            sequence=sequence,
            submission_id=submission,
            kind=EnvelopeKind.child_stopped,
        )
        store.accept(
            QueuedEnvelope(
                "native",
                command.child.conversation_id,
                sequence,
                submission,
                EnvelopeKind.child_stopped,
                payload,
                digest,
            ),
            lambda conn, row: store.runs.record_command_outcome_in_transaction(conn, outcome) or {},
            sequence,
            identity=identity,
        )
        return outcome

    factory = MacRemoteChildFactory(
        assign,
        native,
        lambda start: CanonicalSkillSnapshot(
            tuple(
                CanonicalSkillSource(name, f"---\nname: {name}\ndescription: test\n---\nTest\n")
                for name in ("panels", "panels-worker", "panels-worker-coding")
            ),
            ("panels", "panels-worker", "panels-worker-coding"),
        ),
    )

    def no_local(**kwargs: Any) -> Any:
        raise AssertionError("Recovery must not allocate a local child")

    def core(*, restored: bool = False) -> SqliteProcessConversationSystem:
        # Dispatch uses a direct factory in this synthetic conversation. Recovery uses
        # the production wrapper, whose __call__ must never run for the old identity.
        selected = LocaleBackendChildFactory(db_path, no_local, factory) if restored else factory
        return SqliteProcessConversationSystem(
            store=ConversationStore(db_path),
            backend_child_factories={key: selected for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(db_path),
        )

    def advance_sequence() -> None:
        nonlocal sequence
        sequence += 1

    async def run() -> None:
        system = core()
        await system.start_conversation(
            ConversationStartRequest("c_retry", "scripted", workspace_folder=tmp_path)
        )
        assert isinstance(
            await system.send("c_retry", text_message_content("original"), sender_label="Test"),
            PromptDeliveryUncertain,
        )
        await system.kill("c_retry")
        await system.wait_until_quiescent()
        assert system._conversations["c_retry"].child_is_quarantined
        assert tmp_db.execute("SELECT count(*) FROM executor_command_outcomes").fetchone()[0] == 0
        await system.shutdown()
        restored = core(restored=True)
        await restored.kill("c_retry")
        await restored.wait_until_quiescent()
        assert not restored._conversations["c_retry"].child_is_quarantined
        assert store.runs.child_assignment(references[0]) is None
        assert stops == 2
        recorded = tmp_db.execute("SELECT outcome_json FROM executor_command_outcomes").fetchall()
        assert len(recorded) == 1
        assert json.loads(recorded[0][0])["disposition"] == "succeeded"
        # A separate explicit send uses the same conversation with a fresh identity.
        restored._backend_child_factories = {key: factory for key in ConversationBackendKey}
        await restored.send("c_retry", text_message_content("fresh"), sender_label="Test")
        assert len(references) == 2
        metadata = await restored._store.read_executor_turn("c_retry")
        assert metadata is not None and not json.loads(metadata[2])["delivery_uncertain"]
        await restored.shutdown()
        healthy = core()
        state = healthy._conversations["c_retry"]
        assert state.child_is_quarantined
        assert state.running_turn is None
        assert state.executor_child_reference == references[1]
        assert all(c.child == references[1] for c in state.executor_initial_commands)
        cancel_states.append(state)
        await healthy.kill("c_retry")
        await healthy.wait_until_quiescent()
        assert not state.child_is_quarantined and state.child is None

        async def suppress(*args: Any, **kwargs: Any) -> Any:
            return (), "test_suppression", False

        # Fresh preparation owns the unresolved reservation. Its cleanup must wait
        # until normal suppression finalizes that reservation, then acquire the lock.
        original_native = factory.send_command
        stop_after_resolution = asyncio.Event()

        async def observe_cleanup(command: ChildCommand) -> CommandOutcome:
            if command.operation is ChildOperation.stop:
                assert state.reserved_turn is None
                assert not state.lock.locked()
                stop_after_resolution.set()
            return await original_native(command)

        factory.send_command = observe_cleanup
        original_admission = healthy._admit_dispatch_messages
        healthy._admit_dispatch_messages = suppress
        from planner.conversation.contracts import PromptDeliverySuppressed

        result = await healthy.send(
            "c_retry", text_message_content("suppressed"), sender_label="Test"
        )
        assert isinstance(result, PromptDeliverySuppressed)
        await asyncio.wait_for(stop_after_resolution.wait(), 2)
        await healthy.wait_until_quiescent()
        assert state.child is None and not state.child_is_quarantined
        assert store.runs.child_assignment(references[-1]) is None
        factory.send_command = original_native
        healthy._admit_dispatch_messages = original_admission

        from planner.conversation.backends.contracts import NeedsRebind
        from planner.conversation.events import ConversationTurnEnding

        await healthy.send("c_retry", text_message_content("before rebind"), sender_label="Test")
        old_child = state.child
        old_reference = references[-1]
        assert old_child is not None and state.running_turn is not None
        async with state.lock:
            await healthy._end_turn(
                state, state.running_turn, ConversationTurnEnding.completed, None
            )
            healthy._settle_phase(state)

        async def needs_rebind(*args: Any, **kwargs: Any) -> Any:
            raise NeedsRebind("model change")

        old_child.write_prompt = needs_rebind
        previous_count = len(references)
        # This old child's events use the previous token. Its stop must not await
        # the distinct new reservation that needs the replacement in order to finish.
        await asyncio.wait_for(
            healthy.send("c_retry", text_message_content("after rebind"), sender_label="Test"), 2
        )
        assert len(references) == previous_count + 1
        assert store.runs.child_assignment(old_reference) is None
        assert state.running_turn is not None and not state.child_is_quarantined

        from planner.core.contracts import ErrorCode, PlannerError
        from planner.executor.protocol import BackendEvent, BackendEventMethod, ReceiptDisposition

        # A real refused native publication marks exact cleanup evidence within its
        # receipt transaction. Later successful stops cannot erase that refusal.
        sequence_before_refusal = sequence
        event = BackendEvent(
            references[-1], BackendEventMethod.turn_ended, state.running_turn.token, {}
        )
        payload = cast(JsonObject, encode_value(event))
        submission = "refused-final-event"
        digest = envelope_payload_digest(
            payload,
            (),
            sender_id="native",
            conversation_id="c_retry",
            sequence=sequence_before_refusal + 1,
            submission_id=submission,
            kind=EnvelopeKind.backend_event,
        )

        def refuse(connection: Connection, envelope: QueuedEnvelope) -> JsonObject:
            raise PlannerError(ErrorCode.validation, "final publication refused")

        receipt = store.accept(
            QueuedEnvelope(
                "native",
                "c_retry",
                sequence_before_refusal + 1,
                submission,
                EnvelopeKind.backend_event,
                payload,
                digest,
            ),
            refuse,
            100,
            identity=identity,
        )
        assert receipt.disposition is ReceiptDisposition.refused
        # Keep this one synthetic sender's canonical FIFO sequence aligned.
        advance_sequence()
        poisoned = references[-1]
        await healthy.kill("c_retry")
        assert state.child_is_quarantined and store.runs.child_assignment(poisoned) is not None
        await healthy.kill("c_retry")
        await healthy.wait_until_quiescent()
        assert state.child_is_quarantined and store.runs.cleanup_evidence_uncertain(poisoned)
        await healthy.shutdown()
        quarantined = core(restored=True)
        await quarantined.kill("c_retry")
        await quarantined.wait_until_quiescent()
        assert quarantined._conversations["c_retry"].child_is_quarantined
        assert store.runs.child_assignment(poisoned) is not None
        await quarantined.shutdown()

    asyncio.run(run())


def test_neutral_detach_does_not_stop_or_confirm_a_remote_agent() -> None:
    """Detach releases a proxy; stop still needs the exact native command outcome."""
    from tests.unit.test_executor_native_runtime import canonical_snapshot

    async def exercise() -> None:
        reference = ChildReference("incarnation", "child", "conversation")
        commands: list[ChildCommand] = []
        detached: list[ChildReference] = []

        async def dispatch(command: ChildCommand) -> CommandOutcome:
            commands.append(command)
            return CommandOutcome(command.command_id, reference, CommandDisposition.uncertain, {})

        child: BackendChild = MacRemoteChild(
            reference,
            dispatch,
            lambda start: canonical_snapshot(),
            retire_reference=detached.append,
        )
        assert child.stop_outside_lock
        assert await child.withdraw_delivery()
        await child.detach()
        assert detached == [reference]
        assert commands == []
        with pytest.raises(RemoteCommandUncertain):
            await child.stop()
        assert [command.operation for command in commands] == [ChildOperation.stop]
        assert commands[0].child == reference

    asyncio.run(exercise())
