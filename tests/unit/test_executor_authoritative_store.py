"""Authoritative acceptance, durable choices, generation fencing and file recovery."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, Any, cast

from tests.support.executor_control import MessageReader

from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.protocol import decode_command_arguments

if TYPE_CHECKING:
    pass

import pytest
from tests.support.executor_control import message_link
from tests.support.executor_turns import persist_pending_turn
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry
from tests.unit.test_executor_request_deadlines import peer

from planner.conversation.backends.contracts import TurnToken
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.core.migrations.versions.mac_vps_execution import STATEMENTS
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.protocol import (
    AnswerApplication,
    AskReachability,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    FileManifest,
    FileScope,
    FileUploadRequest,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
    envelope_payload_digest,
)
from planner.executor.vps_inbox import InboxStore

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1)
CHILD = ChildReference("incarnation", "child", "conversation")
ASK = AskReference(CHILD, TurnToken("conversation", 1), "ask", AskKind.permission)


@pytest.fixture
def store(tmp_path: Path) -> InboxStore:
    path = str(tmp_path / "vps.sqlite")
    connection = connect(path)
    try:
        for statement in STATEMENTS:
            connection.execute(statement)
        connection.execute("CREATE TABLE effects (id TEXT PRIMARY KEY)")
    finally:
        connection.close()
    result = InboxStore(path)
    result.runs.begin_connection(IDENTITY, 1000)
    persist_pending_turn(result.runs, ASK)
    return result


def envelope(sequence: int = 1, *, conversation: str = "conversation") -> QueuedEnvelope:
    payload: JsonObject = {"operation": "test"}
    submission_id = f"{conversation}-{sequence}"
    digest = envelope_payload_digest(
        payload,
        (),
        sender_id="sender",
        conversation_id=conversation,
        sequence=sequence,
        submission_id=submission_id,
        kind=EnvelopeKind.cli_write,
    )
    return QueuedEnvelope(
        "sender", conversation, sequence, submission_id, EnvelopeKind.cli_write, payload, digest
    )


def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
    connection.execute("INSERT INTO effects VALUES (?)", (envelope.submission_id,))
    return {"first": envelope.submission_id}


def inventory(
    *children: ChildInventory, identity: ExecutorIdentity = IDENTITY
) -> tuple[ExecutorIdentity, tuple[ChildInventory, ...]]:
    return identity, children


def ready_ask(store: InboxStore) -> None:
    store.runs.publish_ask(ASK, ("allow", "deny"), 1000)
    store.runs.observe_children(*inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ())), 1001)
    store.runs.mark_ready(IDENTITY, 1002)


def test_effect_and_first_result_survive_lost_ack_and_changed_stage(store: InboxStore) -> None:
    first = store.accept(envelope(), effect, 1100, identity=IDENTITY)

    def refuse_now(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        raise AssertionError("Receipt replay must precede current-stage validation")

    assert store.accept(envelope(), refuse_now, 9999, identity=IDENTITY) == first
    connection = connect(store.db_path)
    try:
        assert connection.execute("SELECT count(*) FROM effects").fetchone()[0] == 1
    finally:
        connection.close()


def test_permanent_refusal_rolls_back_partial_effect_and_is_original_result(
    store: InboxStore,
) -> None:
    def refuses(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        effect(connection, envelope)
        raise PlannerError(ErrorCode.stale_claim, "Claim moved")

    receipt = store.accept(envelope(), refuses, 1100, identity=IDENTITY)
    assert receipt.disposition == ReceiptDisposition.refused
    assert store.accept(envelope(), effect, 1200, identity=IDENTITY) == receipt
    connection = connect(store.db_path)
    try:
        assert connection.execute("SELECT count(*) FROM effects").fetchone()[0] == 0
    finally:
        connection.close()


def test_transient_storage_failure_has_no_receipt_and_retry_applies(store: InboxStore) -> None:
    def fails(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        effect(connection, envelope)
        raise sqlite3.OperationalError("disk failure")

    with pytest.raises(sqlite3.OperationalError):
        store.accept(envelope(), fails, 1100, identity=IDENTITY)
    assert store.lookup_receipt("sender", "conversation-1") is None
    assert store.accept(envelope(), effect, 1200, identity=IDENTITY).disposition == "accepted"


def test_order_gap_does_not_block_another_conversation(store: InboxStore) -> None:
    second = store.accept(envelope(2), effect, 1100, identity=IDENTITY)
    store.accept(envelope(conversation="other"), effect, 1100, identity=IDENTITY)
    stale = store.accept(envelope(), effect, 1100, identity=IDENTITY)
    assert stale.result == {}  # Global sender sequences need not be consecutive per conversation.
    assert store.accept(envelope(2), effect, 1200, identity=IDENTITY) == second
    connection = connect(store.db_path)
    try:
        assert [tuple(row) for row in connection.execute("SELECT id FROM effects ORDER BY id")] == [
            ("conversation-2",),
            ("other-1",),
        ]
    finally:
        connection.close()


def test_digest_binds_kind_and_routing_identity(store: InboxStore) -> None:
    original = envelope()
    # Invalid content is a permanent refused receipt, rather than a poison queue head.
    refused = store.accept(
        replace(original, kind=EnvelopeKind.backend_event), effect, 1100, identity=IDENTITY
    )
    assert refused.disposition is ReceiptDisposition.refused
    assert "digest" in str(refused.result)
    assert (
        store.accept(
            replace(original, kind=EnvelopeKind.backend_event), effect, 1200, identity=IDENTITY
        )
        == refused
    )


def test_stale_generation_cannot_write_after_replacement_and_old_close_is_harmless(
    store: InboxStore,
) -> None:
    replacement = replace(IDENTITY, connection_generation=2)
    store.runs.begin_connection(replacement, 1100)
    with pytest.raises(PlannerError, match="stale"):
        store.accept(envelope(), effect, 1100, identity=IDENTITY)
    store.runs.mark_lost(IDENTITY, 1200)
    store.accept(envelope(), effect, 1200, identity=replacement)
    store.runs.mark_lost(replacement, 1300)
    store.runs.mark_lost(replacement, 1400)
    connection = connect(store.db_path)
    try:
        assert (
            connection.execute("SELECT lost_at_ms FROM executor_connections").fetchone()[0] == 1300
        )
    finally:
        connection.close()


def test_first_answer_after_loss_is_durable_without_current_generation_or_readiness(
    store: InboxStore,
) -> None:
    ready_ask(store)
    store.runs.mark_lost(IDENTITY, 1100)
    with pytest.raises(PlannerError, match="option"):
        store.runs.choose_answer(ASK, "not-an-option", 999, 1102)
    answer = store.runs.choose_answer(ASK, "allow", 999, 1103)
    assert answer.accepted_at_vps_ms == 1103
    assert store.runs.choose_answer(ASK, "allow", 1, 1110) == answer
    assert not store.runs.answer_is_deliverable(answer, IDENTITY)
    with pytest.raises(PlannerError, match="already"):
        store.runs.choose_answer(ASK, "deny", 1, 1110)


@pytest.mark.parametrize(
    "closure",
    [
        "assignment",
        "ended",
        "source_retired",
        "delivery_uncertain",
        "pending",
        "child",
        "turn",
        "missing",
        "malformed",
    ],
)
def test_first_answer_requires_open_assignment_and_exact_persisted_pending_turn(
    store: InboxStore,
    closure: str,
) -> None:
    ready_ask(store)
    if closure == "assignment":
        store.runs.close_child(CHILD, 1100)
    elif closure in {"ended", "source_retired", "delivery_uncertain"}:
        persist_pending_turn(store.runs, ASK, runtime_overrides={closure: True})
    elif closure == "pending":
        persist_pending_turn(store.runs, ASK, runtime_overrides={"pending_permission_ask_ids": []})
    elif closure == "child":
        persist_pending_turn(store.runs, replace(ASK, child=replace(CHILD, child_id="replacement")))
    elif closure == "turn":
        persist_pending_turn(store.runs, replace(ASK, turn=TurnToken("conversation", 2)))
    else:
        connection = connect(store.db_path)
        try:
            if closure == "missing":
                connection.execute("DELETE FROM executor_core_turns")
            else:
                connection.execute("UPDATE executor_core_turns SET runtime_json='{}'")
        finally:
            connection.close()
    with pytest.raises(PlannerError):
        store.runs.choose_answer(ASK, "allow", 1, 1200)
    assert store.runs.recorded_answer(ASK) is None


def test_identical_answer_receipt_survives_closure_and_application(store: InboxStore) -> None:
    ready_ask(store)
    answer = store.runs.choose_answer(ASK, "allow", 1, 1100)
    store.runs.close_child(CHILD, 1101)
    assert not store.runs.answer_is_deliverable(answer, IDENTITY)
    store.runs.record_application(AnswerApplication(answer.answer_id, ASK, 1102), identity=IDENTITY)
    assert store.runs.choose_answer(ASK, "allow", 2, 9999) == answer
    with pytest.raises(PlannerError, match="already"):
        store.runs.choose_answer(ASK, "deny", 2, 9999)
    assert not store.runs.answer_is_deliverable(answer, IDENTITY)


def test_concurrent_first_choices_commit_one_original_receipt(store: InboxStore) -> None:
    ready_ask(store)
    store.runs.mark_lost(IDENTITY, 1100)

    barrier = Barrier(2)

    def choose(option: str) -> object:
        barrier.wait(timeout=2)
        try:
            return store.runs.choose_answer(ASK, option, 1, 1200)
        except PlannerError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(choose, ("allow", "deny")))
    assert sum(isinstance(result, PlannerError) for result in outcomes) == 1
    answer = store.runs.recorded_answer(ASK)
    assert answer is not None and answer in outcomes
    assert store.runs.choose_answer(ASK, answer.option_id or "", 1, 9999) == answer


@pytest.mark.parametrize("terminal", [AskReachability.child_gone, AskReachability.callback_gone])
def test_terminal_evidence_survives_loss_reconnect_reconcile_and_late_observation(
    store: InboxStore,
    terminal: AskReachability,
) -> None:
    ready_ask(store)
    store.runs.observe_children(
        *inventory()
        if terminal == AskReachability.child_gone
        else inventory(ChildInventory(CHILD, ASK.turn, (), ())),
        1100,
    )
    store.runs.mark_lost(IDENTITY, 1101)
    replacement = replace(IDENTITY, connection_generation=2)
    store.runs.begin_connection(replacement, 1200)
    store.runs.observe_children(
        *inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ()), identity=replacement), 1201
    )
    connection = connect(store.db_path)
    try:
        store.runs.observe_callback_in_transaction(connection, ASK, replacement, 1202)
    finally:
        connection.close()
    evidence = store.runs.ask_evidence(ASK)
    assert evidence is not None and evidence.reachability == terminal
    with pytest.raises(PlannerError):
        store.runs.choose_answer(ASK, "allow", 2, 1203)


def test_old_durable_answer_reconciles_once_and_retains_application(
    store: InboxStore,
) -> None:
    ready_ask(store)
    answer = store.runs.choose_answer(ASK, "allow", 1, 1100)
    store.runs.mark_lost(IDENTITY, 1101)
    replacement = replace(IDENTITY, connection_generation=2)
    store.runs.begin_connection(replacement, 3600000)
    store.runs.observe_children(
        *inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ()), identity=replacement), 3600001
    )
    assert store.runs.unapplied_answers() == (answer,)
    assert store.runs.ask_evidence(ASK).reachability == AskReachability.reachable  # type: ignore[union-attr]
    store.runs.record_application(
        AnswerApplication(answer.answer_id, ASK, 3600110), identity=replacement
    )
    store.runs.observe_children(*inventory(identity=replacement), 3600300)
    assert store.runs.unapplied_answers() == ()
    evidence = store.runs.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.applied_at_mac_ms == 3600110


def test_missing_child_and_missing_callback_publish_distinct_evidence(store: InboxStore) -> None:
    ready_ask(store)
    store.runs.observe_children(*inventory(ChildInventory(CHILD, ASK.turn, (), ())), 1200)
    evidence = store.runs.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.reachability == AskReachability.callback_gone
    store.runs.observe_children(*inventory(), 1300)
    evidence = store.runs.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.reachability == AskReachability.callback_gone
    with pytest.raises(PlannerError, match="closed or unknown"):
        store.runs.choose_answer(ASK, "allow", 1, 1400)


def manifest(contents: bytes) -> FileManifest:
    return FileManifest(
        FileScope.ticket,
        "test-ticket",
        "result.bin",
        hashlib.sha256(contents).hexdigest(),
        len(contents),
    )


def test_session_file_cleanup_failure_still_releases_transport_root(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import os
    import tempfile
    from typing import cast
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from planner.core.clock import RealClock
    from planner.executor.link_files import GenerationTransfers
    from planner.executor.protocol import (
        ConnectionState,
        ExecutorHealth,
        WireMessage,
        as_object,
        encode_message,
    )
    from planner.executor.vps_transport import VpsExecutor

    async def run() -> None:
        endpoint = VpsExecutor(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.inbox.store = store
        identity = replace(IDENTITY, executor_incarnation=str(uuid4()))
        reader = MessageReader()
        reader.feed_data(
            encode_message(
                WireMessage(
                    WireMessageKind.hello,
                    "hello",
                    as_object(
                        ExecutorHealth(
                            identity, os.getpid(), ConnectionState.reconciling, False, 1, None
                        )
                    ),
                )
            )
        )
        reader.feed_eof()
        service = Mock()
        service.close = AsyncMock(side_effect=OSError("service cleanup failed"))
        root = tempfile.TemporaryDirectory(dir=tmp_path)
        root_path = Path(root.name)

        async def setup_generation(*_: object) -> None:
            endpoint._transports[identity] = cast(GenerationTransfers, service)
            endpoint._transport_roots[identity] = root
            endpoint._upload_transfers[(identity, "transfer")] = (
                "request",
                FileUploadRequest(manifest(b"bytes"), CHILD),
            )

        writer = peer()
        writer.send = AsyncMock(side_effect=setup_generation)
        monkeypatch.setattr(endpoint.catalog, "_measure_clock", AsyncMock())
        try:
            with pytest.raises(OSError, match="service cleanup failed"):
                await endpoint._session(reader, writer)
            service.close.assert_awaited_once()
            assert not root_path.exists()
            assert not endpoint._transports and not endpoint._transport_roots
            assert not endpoint._upload_transfers
            assert endpoint.runs.link is None and not endpoint.runs.ready
        finally:
            root.cleanup()
            for task in tuple(endpoint.catalog._tasks):
                task.cancel()
            await asyncio.gather(*endpoint.catalog._tasks, return_exceptions=True)

    asyncio.run(run())


def test_cancelled_input_snapshot_is_removed_after_copy_settles(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancellation before a descriptor exists must still reclaim the completed snapshot."""
    import asyncio
    import threading
    from types import SimpleNamespace

    from planner.conversation.contracts import PromptDeliveryMode
    from planner.conversation.message_content import MessageFile
    from planner.core.clock import RealClock
    from planner.executor.files import snapshot_file
    from planner.executor.link_files import GenerationTransfers
    from planner.executor.protocol import WritePrompt
    from planner.executor.vps_transport import VpsExecutor

    async def run() -> None:
        store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
        endpoint = VpsExecutor(store.db_path, RealClock())
        source = tmp_path / "inputs" / "file"
        source.parent.mkdir()
        source.write_bytes(b"input")
        endpoint.message_files = cast(Any, SimpleNamespace(path_of=lambda *args: source))
        service = GenerationTransfers(IDENTITY, tmp_path / "transfers", lambda: True)
        endpoint._transports[IDENTITY] = service
        command = ChildCommand(
            "prompt",
            CHILD,
            ChildOperation.write_prompt,
            ASK.turn,
            WritePrompt(
                content=(MessageFile("file", "text/plain", "file", 5),),
                sender_content=(),
                sender_label="owner",
                mode=PromptDeliveryMode.queue,
            ),
        )
        endpoint.runs.pending_commands["prompt"] = (
            command,
            asyncio.get_running_loop().create_future(),
        )
        entered, release = threading.Event(), threading.Event()

        def held(**kwargs: Any) -> Any:
            entered.set()
            assert release.wait(5)
            return snapshot_file(**kwargs)

        monkeypatch.setattr("planner.executor.vps_transport.snapshot_file", held)
        copying = asyncio.create_task(
            endpoint._file(
                WireMessage(
                    WireMessageKind.get_file,
                    "input",
                    {
                        "command_id": "prompt",
                        "stored_file_id": "file",
                    },
                ),
                IDENTITY,
            )
        )
        assert await asyncio.to_thread(entered.wait, 2)
        copying.cancel()
        await asyncio.sleep(0)
        assert not copying.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await copying
        assert list((tmp_path / ".executor-snapshots").iterdir()) == []
        assert not service.transfers
        await service.close()

    asyncio.run(run())


def test_stop_hints_wait_for_exact_original_canonical_receipt(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json
    from unittest.mock import AsyncMock

    from planner.core.clock import RealClock
    from planner.executor.protocol import ChildCommand, WireMessage, as_object, encode_value
    from planner.executor.vps_transport import VpsExecutor

    async def run() -> None:
        command = ChildCommand(
            "retire",
            CHILD,
            ChildOperation.stop,
            None,
            decode_command_arguments(ChildOperation.stop, {}),
        )
        outcome = CommandOutcome(command.command_id, CHILD, CommandDisposition.succeeded, {})
        persist_pending_turn(
            store.runs, ASK, runtime_overrides={"initial_commands": [encode_value(command)]}
        )
        endpoint = VpsExecutor(store.db_path, RealClock())
        endpoint.inbox.store = store
        endpoint.runs.identity = IDENTITY
        writer = peer()
        endpoint.runs.link = message_link(writer, IDENTITY)
        future = asyncio.get_running_loop().create_future()
        endpoint.runs.pending_commands[command.command_id] = command, future
        send = AsyncMock()
        monkeypatch.setattr(endpoint.runs.link._writer, "send", send)
        connection = connect(store.db_path)
        try:
            store.runs.record_command_outcome_in_transaction(connection, outcome)
        finally:
            connection.close()
        submission = "stopped_" + hashlib.sha256(command.command_id.encode()).hexdigest()
        # A conflicting accepted submission cannot stand in for this outcome.
        unrelated = replace(envelope(), submission_id=submission)
        unrelated = replace(
            unrelated,
            payload_sha256=envelope_payload_digest(
                unrelated.payload,
                (),
                sender_id=unrelated.sender_id,
                conversation_id=unrelated.conversation_id,
                sequence=1,
                submission_id=submission,
                kind=unrelated.kind,
            ),
        )
        store.accept(unrelated, effect, 1100, identity=IDENTITY)
        assert not store.runs.has_accepted_command_outcome(outcome)
        await endpoint._handle_message(
            WireMessage(WireMessageKind.command_outcome, command.command_id, as_object(outcome)),
            IDENTITY,
            endpoint.runs.link,
        )
        assert not future.done() and send.await_count == 0
        await endpoint.runs._resolve_command(outcome)
        assert not future.done()
        assert store.runs.child_assignment(CHILD) is not None
        payload = cast(JsonObject, encode_value(outcome))
        canonical = QueuedEnvelope(
            "canonical-sender",
            CHILD.conversation_id,
            1,
            submission,
            EnvelopeKind.child_stopped,
            payload,
            envelope_payload_digest(
                payload,
                (),
                sender_id="canonical-sender",
                conversation_id=CHILD.conversation_id,
                sequence=1,
                submission_id=submission,
                kind=EnvelopeKind.child_stopped,
            ),
        )
        receipt = await endpoint.inbox.accept(canonical, IDENTITY)
        assert receipt.disposition is ReceiptDisposition.accepted
        assert future.done() and future.result() == outcome
        assert store.runs.has_accepted_command_outcome(outcome)
        assert store.lookup_receipt(canonical.sender_id, submission) == receipt
        # The accepted stop preserves its original outcome and leaves canonical
        # assignment closure to the core retirement transaction.
        connection = connect(store.db_path)
        try:
            assert json.loads(
                connection.execute(
                    "SELECT outcome_json FROM executor_command_outcomes WHERE command_id=?",
                    (command.command_id,),
                ).fetchone()[0]
            ) == encode_value(outcome)
        finally:
            connection.close()

    from typing import cast

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind",
    [
        EnvelopeKind.backend_event,
        EnvelopeKind.child_stopped,
        EnvelopeKind.answer_application,
        EnvelopeKind.cli_write,
    ],
)
def test_refused_native_evidence_marker_survives_domain_rollback_and_metadata_turns(
    store: InboxStore, kind: EnvelopeKind
) -> None:
    import json
    from typing import cast

    from planner.conversation.storage import ConversationStore
    from planner.executor.protocol import BackendEvent, BackendEventMethod, encode_value

    value: object
    if kind is EnvelopeKind.backend_event:
        value = BackendEvent(CHILD, BackendEventMethod.turn_ended, ASK.turn, {})
    elif kind is EnvelopeKind.child_stopped:
        value = CommandOutcome("late-result", CHILD, CommandDisposition.succeeded, {})
    elif kind is EnvelopeKind.answer_application:
        value = AnswerApplication("answer", ASK, 1000)
    else:
        value = {"operation": "refused-domain-action"}
    payload = cast(JsonObject, encode_value(value))
    queued = replace(envelope(), kind=kind, payload=payload)
    queued = replace(
        queued,
        payload_sha256=envelope_payload_digest(
            payload,
            (),
            sender_id=queued.sender_id,
            conversation_id=queued.conversation_id,
            sequence=queued.sequence,
            submission_id=queued.submission_id,
            kind=kind,
        ),
    )

    def refuse(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        effect(connection, envelope)
        raise PlannerError(ErrorCode.validation, "required publication refused", {})

    receipt = store.accept(queued, refuse, 1100, identity=IDENTITY)
    assert receipt.disposition is ReceiptDisposition.refused
    assert store.runs.cleanup_evidence_uncertain(CHILD) is (kind is not EnvelopeKind.cli_write)
    connection = connect(store.db_path)
    try:
        assert connection.execute("SELECT count(*) FROM effects").fetchone()[0] == 0
        old = connection.execute(
            "SELECT child_json,turn_json,runtime_json FROM executor_core_turns"
        ).fetchone()
        metadata = (
            old[0],
            json.dumps(encode_value(replace(ASK.turn, turn_number=2))),
            json.dumps({"ended": True, "explicit_reply_recipients": []}),
        )
        ConversationStore.save_executor_turn_in_transaction(
            connection, CHILD.conversation_id, metadata
        )
    finally:
        connection.close()
    assert store.runs.cleanup_evidence_uncertain(CHILD) is (kind is not EnvelopeKind.cli_write)
    assert store.lookup_receipt(queued.sender_id, queued.submission_id) == receipt


def test_retirement_metadata_is_monotonic_and_stale_child_cannot_replace_fresh(
    store: InboxStore,
) -> None:
    import json

    from planner.conversation.storage import ConversationStore
    from planner.executor.protocol import encode_value

    connection = connect(store.db_path)
    try:
        old = connection.execute(
            "SELECT child_json,turn_json,runtime_json FROM executor_core_turns"
        ).fetchone()
        metadata = (old[0], old[1], json.dumps({"ended": True, "source_retired": True}))
        ConversationStore.save_executor_turn_in_transaction(
            connection, CHILD.conversation_id, metadata
        )
        unmarked = (old[0], json.dumps(encode_value(replace(ASK.turn, turn_number=2))), old[2])
        ConversationStore.save_executor_turn_in_transaction(
            connection, CHILD.conversation_id, unmarked
        )
        assert json.loads(
            connection.execute("SELECT runtime_json FROM executor_core_turns").fetchone()[0]
        )["source_retired"]
    finally:
        connection.close()
    connection = connect(store.db_path)
    try:
        newest_runtime = {
            "ended": False,
            "delivery_uncertain": False,
            "initial_commands": ["new-command"],
            "callbacks_revoked": False,
        }
        newest = (
            old[0],
            json.dumps(encode_value(replace(ASK.turn, turn_number=3))),
            json.dumps(newest_runtime),
        )
        ConversationStore.save_executor_turn_in_transaction(
            connection, CHILD.conversation_id, newest
        )
        ConversationStore.save_executor_turn_in_transaction(
            connection, CHILD.conversation_id, metadata
        )
        row = connection.execute(
            "SELECT turn_json,runtime_json FROM executor_core_turns"
        ).fetchone()
        assert json.loads(row[0])["turn_number"] == 3
        retained = json.loads(row[1])
        assert retained["initial_commands"] == ["new-command"]
        assert retained["delivery_uncertain"] is False
        assert retained["callbacks_revoked"] is False
        assert retained["source_retired"] is True
    finally:
        connection.close()
    fresh = replace(CHILD, child_id="fresh")
    store.runs.close_child(CHILD, 1200)
    store.runs.register_child(fresh, "ticket", "test-ticket", 1201)
    connection = connect(store.db_path)
    try:
        fresh_metadata = (json.dumps(encode_value(fresh)), unmarked[1], old[2])
        ConversationStore.save_executor_turn_in_transaction(
            connection, CHILD.conversation_id, fresh_metadata
        )
        ConversationStore.save_executor_turn_in_transaction(
            connection, CHILD.conversation_id, unmarked
        )
        row = connection.execute(
            "SELECT child_json,runtime_json FROM executor_core_turns"
        ).fetchone()
        assert json.loads(row[0]) == encode_value(fresh)
        assert not json.loads(row[1]).get("source_retired")
        assert not json.loads(row[1]).get("cleanup_evidence_uncertain")
    finally:
        connection.close()


def test_postcommit_error_preserves_durable_effect_and_receipt(
    store: InboxStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import store as store_module

    original_connect = connect
    calls: list[str] = []
    error = RuntimeError("postcommit participant failure")

    class FailingCommit:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self.connection = connection

        def __getattr__(self, name: str) -> Any:
            return getattr(self.connection, name)

        def commit(self) -> None:
            calls.append("commit")
            self.connection.commit()
            raise error

        def rollback(self) -> None:
            calls.append("rollback")
            self.connection.rollback()

    row = envelope()
    with monkeypatch.context() as failure:
        failure.setattr(store_module, "connect", lambda path: FailingCommit(original_connect(path)))
        with pytest.raises(RuntimeError) as raised:
            store.accept(row, effect, 123, identity=IDENTITY)
    assert raised.value is error
    assert calls == ["commit", "rollback"]
    saved = store.lookup_receipt(row.sender_id, row.submission_id)
    assert saved is not None and saved.disposition is ReceiptDisposition.accepted
    assert store.accept(row, effect, 124, identity=IDENTITY) == saved
    connection = connect(store.db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    finally:
        connection.close()


def test_preflight_never_establishes_acceptance(store: InboxStore) -> None:
    assert store.preflight(envelope(), identity=IDENTITY) is None
    assert store.lookup_receipt("sender", "conversation-1") is None
    receipt = store.accept(envelope(), effect, 1100, identity=IDENTITY)
    assert receipt == store.lookup_receipt("sender", "conversation-1")


def test_control_frame_progresses_while_envelope_waits(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock, Mock

    from tests.unit.test_executor_request_deadlines import peer

    from planner.core.clock import RealClock
    from planner.executor.protocol import AcceptanceReceipt, WireMessage, encode_value
    from planner.executor.vps_transport import VpsExecutor

    async def exercise() -> None:
        endpoint = VpsExecutor(str(tmp_path / "unused.sqlite"), RealClock())
        writer = peer()
        endpoint.runs.identity, endpoint.runs.link = IDENTITY, message_link(writer, IDENTITY)
        endpoint.inbox.store = Mock()
        endpoint.runs.store = endpoint.inbox.store.runs
        endpoint.inbox.store.runs.unapplied_answers.return_value = ()
        from planner.executor.protocol import ReconnectResult, as_object

        endpoint.runs.link.request = AsyncMock(  # type: ignore[method-assign]
            return_value=WireMessage(
                WireMessageKind.reconnect_result,
                "ask",
                as_object(ReconnectResult(IDENTITY, (), ())),
            )
        )
        endpoint.bind_conversations(cast(Any, SimpleNamespace()))
        head = envelope()
        frame = WireMessage(WireMessageKind.envelope, "head", cast(JsonObject, encode_value(head)))
        control = WireMessage(WireMessageKind.reconciliation_ready, "control", {})
        entered, release = asyncio.Event(), asyncio.Event()

        async def accept(envelope: QueuedEnvelope, identity: ExecutorIdentity) -> AcceptanceReceipt:
            row = envelope
            entered.set()
            await release.wait()
            return AcceptanceReceipt(
                row.sender_id,
                row.submission_id,
                row.payload_sha256,
                ReceiptDisposition.accepted,
                {"applied": True},
                123,
            )

        endpoint.inbox.accept = accept  # type: ignore[method-assign]
        task = asyncio.create_task(endpoint._handle_message(frame, IDENTITY, endpoint.runs.link))
        await entered.wait()
        await endpoint._handle_message(control, IDENTITY, endpoint.runs.link)
        assert not task.done()
        assert endpoint.runs.ready
        release.set()
        await task
        writer.close.assert_not_called()
        assert writer.send.call_count == 2

    asyncio.run(exercise())


def test_superseded_ingress_cannot_reach_handler_or_close_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Superseded ingress cannot execute a domain handler or close the replacement writer."""
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from tests.unit.test_executor_request_deadlines import peer
    from tests.unit.test_machine_ui_reads import _hello

    from planner.core.clock import RealClock
    from planner.executor import vps_transport as vps
    from planner.executor.protocol import WireMessage

    async def exercise() -> None:
        endpoint = vps.VpsExecutor(str(tmp_path / "unused.sqlite"), RealClock())
        original, replacement = peer(), peer()
        endpoint.inbox.store = Mock()
        endpoint.runs.store = endpoint.inbox.store.runs
        endpoint.catalog._measure_clock = AsyncMock()  # type: ignore[method-assign]
        endpoint._close_transport = AsyncMock()  # type: ignore[method-assign]
        endpoint._handle = AsyncMock()  # type: ignore[method-assign]
        calls = 0
        frame = WireMessage(WireMessageKind.error, "superseded", {})

        async def read() -> WireMessage:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _hello()
            assert endpoint.runs.link is not None
            endpoint.runs.link.close()
            endpoint.runs.link = message_link(replacement, IDENTITY)
            return frame

        original.receive = AsyncMock(side_effect=read)
        await endpoint._session(original, original)
        endpoint._handle.assert_not_called()
        replacement.close.assert_not_called()
        original.close.assert_called_once_with()
        await asyncio.gather(*endpoint.catalog._tasks)

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid", [None, "generation", "child", "command_ids", "unfinished"])
def test_one_reconnect_ask_resolves_finished_and_keeps_inflight_commands(
    store: InboxStore, monkeypatch: pytest.MonkeyPatch, invalid: str | None
) -> None:
    """Gap: one snapshot replaces outcome ACK/chunk aggregation without granting stale authority."""
    import asyncio
    from unittest.mock import AsyncMock

    from planner.core.clock import RealClock
    from planner.executor.protocol import CommandState, ReconnectResult, as_object
    from planner.executor.vps_transport import VpsExecutor

    async def run() -> None:
        endpoint = VpsExecutor(store.db_path, RealClock())
        endpoint.runs.identity, endpoint.runs.link = IDENTITY, message_link(peer(), IDENTITY)
        commands = tuple(
            ChildCommand(
                phase,
                CHILD,
                ChildOperation.cancel_running_turn,
                None,
                decode_command_arguments(ChildOperation.cancel_running_turn, {}),
            )
            for phase in ("unknown", "preparing", "invoking", "finished")
        )
        for command in commands:
            endpoint.runs.pending_commands[command.command_id] = (
                command,
                asyncio.get_running_loop().create_future(),
            )
        outcome = CommandOutcome("finished", CHILD, CommandDisposition.succeeded, {})
        states: tuple[CommandState, ...] = (
            CommandState("unknown", "unknown"),
            CommandState("preparing", "preparing"),
            CommandState("invoking", "invoking"),
        )
        states += (CommandState("finished", "finished", outcome),)
        result = ReconnectResult(IDENTITY, states, ())
        if invalid == "generation":
            result = replace(result, identity=replace(IDENTITY, connection_generation=2))
        elif invalid == "child":
            result = replace(
                result,
                commands=states[:-1]
                + (
                    replace(
                        states[-1], outcome=replace(outcome, child=replace(CHILD, child_id="other"))
                    ),
                ),
            )
        elif invalid == "command_ids":
            result = replace(result, commands=states[:-1])
        elif invalid == "unfinished":
            result = replace(result, commands=(replace(states[0], outcome=outcome),) + states[1:])
        request = AsyncMock(
            return_value=WireMessage(WireMessageKind.reconnect_result, "ask", as_object(result))
        )
        monkeypatch.setattr(endpoint.runs.link, "request", request)
        if invalid is not None:
            with pytest.raises(ValueError):
                await endpoint.runs._reconnect(IDENTITY, endpoint.runs.link)
            assert all(not f.done() for _, f in endpoint.runs.pending_commands.values())
        else:
            assert await endpoint.runs._reconnect(IDENTITY, endpoint.runs.link) == {"unknown"}
            assert endpoint.runs.pending_commands["finished"][1].result() == outcome
            assert all(
                not endpoint.runs.pending_commands[key][1].done()
                for key in ("unknown", "preparing", "invoking")
            )
        request.assert_awaited_once_with(
            WireMessageKind.reconnect_ask, {"command_ids": [c.command_id for c in commands]}
        )

    asyncio.run(run())


def test_ready_ack_includes_stop_registered_after_reconnect_ask(
    store: InboxStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: retirement can register while the ready ACK yields, after the ask snapshot."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.unit.test_executor_request_deadlines import peer

    from planner.core.clock import RealClock
    from planner.executor.vps_transport import VpsExecutor

    async def run() -> None:
        endpoint = VpsExecutor(store.db_path, RealClock())
        writer = peer()
        endpoint.runs.identity, endpoint.runs.link = IDENTITY, message_link(writer, IDENTITY)
        endpoint.bind_conversations(cast(Any, SimpleNamespace()))
        command = ChildCommand(
            "late-stop",
            CHILD,
            ChildOperation.stop,
            None,
            decode_command_arguments(ChildOperation.stop, {}),
        )
        caller: asyncio.Task[CommandOutcome] | None = None
        outcome = CommandOutcome(command.command_id, CHILD, CommandDisposition.uncertain, {})

        async def ready(message: WireMessage) -> None:
            nonlocal caller
            assert message.kind is WireMessageKind.reconciliation_ready
            assert not endpoint.runs.ready
            caller = asyncio.create_task(endpoint.runs.send_command(command))
            await asyncio.sleep(0)
            assert command.command_id in endpoint.runs.pending_commands

        async def emit(*args: object) -> bool:
            assert endpoint.runs.ready
            endpoint.runs.pending_commands[command.command_id][1].set_result(outcome)
            return True

        writer.send.side_effect = ready
        monkeypatch.setattr(endpoint.runs, "_reconnect", AsyncMock(return_value=set()))
        emitted = AsyncMock(side_effect=emit)
        monkeypatch.setattr(endpoint.runs, "_emit_command", emitted)
        try:
            await endpoint._handle_message(
                WireMessage(WireMessageKind.reconciliation_ready, "ready", {}),
                IDENTITY,
                endpoint.runs.link,
            )
            emitted.assert_awaited_once_with(command, IDENTITY, endpoint.runs.link)
            assert caller is not None and await asyncio.wait_for(caller, 1) == outcome
        finally:
            if caller is not None and not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)

    asyncio.run(run())


def file_envelope(sequence: int, file: FileManifest) -> QueuedEnvelope:
    from planner.executor.protocol import encode_value

    payload: JsonObject = {"child": encode_value(CHILD)}
    row = replace(
        envelope(sequence),
        kind=EnvelopeKind.backend_event,
        payload=payload,
        file_dependencies=(file,),
    )
    return replace(
        row,
        payload_sha256=envelope_payload_digest(
            payload,
            row.file_dependencies,
            sender_id=row.sender_id,
            conversation_id=row.conversation_id,
            sequence=row.sequence,
            submission_id=row.submission_id,
            kind=row.kind,
        ),
    )


def test_file_install_precedes_effect_and_old_item_cannot_overwrite_newer(
    store: InboxStore, tmp_path: Path
) -> None:
    store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
    receipts = []
    rows = []
    for sequence, data in enumerate((b"old", b"new"), 1):
        file = manifest(data)
        source = tmp_path / "source"
        source.write_bytes(data)
        with source.open("r+b", buffering=0) as retained:
            store.store_upload(file, source)
            retained.write(b"bad")
        assert not store.file_target(file).exists() or sequence == 2
        row = file_envelope(sequence, file)

        def write(
            connection: sqlite3.Connection,
            envelope: QueuedEnvelope,
            file: FileManifest = file,
            data: bytes = data,
        ) -> JsonObject:
            assert store.file_target(file).read_bytes() == data
            return effect(connection, envelope)

        receipts.append(store.accept(row, write, 1, identity=IDENTITY))
        rows.append(row)
    assert store.accept(rows[0], effect, 2, identity=IDENTITY).result == {}
    assert store.file_target(rows[-1].file_dependencies[0]).read_bytes() == b"new"


def test_rename_before_database_rollback_recovers_without_upload(
    store: InboxStore, tmp_path: Path
) -> None:
    store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
    file = manifest(b"complete")
    source = tmp_path / "source"
    source.write_bytes(b"complete")
    store.store_upload(file, source)
    row = file_envelope(1, file)

    def crash(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        assert store.file_target(file).read_bytes() == b"complete"
        raise OSError("power loss after rename")

    with pytest.raises(OSError, match="power loss"):
        store.accept(row, crash, 1, identity=IDENTITY)
    assert store.preflight(row, identity=IDENTITY) is None
    assert (
        store.accept(row, effect, 2, identity=IDENTITY).disposition is ReceiptDisposition.accepted
    )


def test_upload_checks_bytes_path_and_assignment(store: InboxStore, tmp_path: Path) -> None:
    store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
    file = manifest(b"complete")
    source = tmp_path / "source"
    for data in (b"short", b"corrupt!", b"complete plus"):
        source.write_bytes(data)
        with pytest.raises(ValueError):
            store.store_upload(file, source)
    source.write_bytes(b"complete")
    with pytest.raises(ValueError, match="unsafe"):
        store.store_upload(replace(file, relative_path="../escape"), source)
    outside = replace(file, owner_id="other")
    store.store_upload(outside, source)
    refused = store.accept(file_envelope(1, outside), effect, 1, identity=IDENTITY)
    assert refused.disposition is ReceiptDisposition.refused
    assert not store.file_target(outside).exists()


def test_upload_cancellation_keeps_transfer_until_private_copy_settles(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replaced completion exchange must still own an admitted copy on cancellation."""
    import asyncio
    import threading
    from collections.abc import AsyncIterator

    from planner.core.clock import RealClock
    from planner.executor.link_files import GenerationTransfers
    from planner.executor.protocol import FileUploadRequest
    from planner.executor.vps_transport import VpsExecutor

    async def run() -> None:
        endpoint = VpsExecutor(store.db_path, RealClock())
        service = GenerationTransfers(IDENTITY, tmp_path / "transfers", lambda: True)
        endpoint._transports[IDENTITY] = service
        file = manifest(b"complete")
        slot = service.create_upload(file)
        endpoint._upload_transfers[IDENTITY, slot.transfer_id] = (
            "request",
            FileUploadRequest(file, CHILD),
        )

        async def chunks() -> AsyncIterator[bytes]:
            yield b"complete"

        await service.upload(slot.transfer_id, chunks())
        entered, release = threading.Event(), threading.Event()
        original = endpoint.inbox.store.store_upload

        def held(manifest: FileManifest, source: Path) -> None:
            entered.set()
            assert release.wait(5)
            assert source.exists()
            original(manifest, source)

        monkeypatch.setattr(endpoint.inbox.store, "store_upload", held)
        copying = asyncio.create_task(endpoint.store_upload(IDENTITY, slot.transfer_id))
        assert await asyncio.to_thread(entered.wait, 2)
        copying.cancel()
        await asyncio.sleep(0)
        assert not copying.done() and slot.transfer_id in service.transfers
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await copying
        assert slot.transfer_id not in service.transfers
        assert not endpoint._receiver_work
        assert not endpoint.inbox.store.file_target(file).exists()
        endpoint.inbox.store.install_files((file,))
        assert endpoint.inbox.store.file_target(file).read_bytes() == b"complete"
        await service.close()

    asyncio.run(run())
