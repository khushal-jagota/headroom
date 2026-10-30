"""Authoritative acceptance, durable choices, generation fencing and file recovery."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from planner.executor.vps import ExecutorEndpoint

import pytest
from tests.support.executor_turns import persist_pending_turn
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

from planner.conversation.backends.contracts import TurnToken
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.core.migrations.versions.mac_vps_execution import STATEMENTS
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AnswerApplication,
    AskKind,
    AskReachability,
    AskReference,
    ChildCommand,
    ChildInventory,
    ChildOperation,
    ChildReference,
    CommandDisposition,
    CommandOutcome,
    EnvelopeKind,
    ExecutorIdentity,
    FileManifest,
    FileScope,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    ReconciliationInventory,
    WireMessage,
    WireMessageKind,
)
from planner.executor.journal import envelope_payload_digest
from planner.executor.publication import PublicationReceiver
from planner.executor.store import ExecutorStore

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1)
CHILD = ChildReference("incarnation", "child", "conversation")
ASK = AskReference(CHILD, TurnToken("conversation", 1), "ask", AskKind.permission)


@pytest.fixture
def store(tmp_path: Path) -> ExecutorStore:
    path = str(tmp_path / "vps.sqlite")
    connection = connect(path)
    try:
        for statement in STATEMENTS:
            connection.execute(statement)
        connection.execute("CREATE TABLE effects (id TEXT PRIMARY KEY)")
    finally:
        connection.close()
    result = ExecutorStore(path)
    result.begin_connection(IDENTITY, 1000)
    persist_pending_turn(result, ASK)
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
) -> ReconciliationInventory:
    return ReconciliationInventory(identity, children, (), ())


def ready_ask(store: ExecutorStore) -> None:
    store.publish_ask(ASK, ("allow", "deny"), 1000)
    store.reconcile(inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ())), 1001)
    store.mark_ready(IDENTITY, 1002)


def test_effect_and_first_result_survive_lost_ack_and_changed_stage(store: ExecutorStore) -> None:
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
    store: ExecutorStore,
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


def test_transient_storage_failure_has_no_receipt_and_retry_applies(store: ExecutorStore) -> None:
    def fails(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        effect(connection, envelope)
        raise sqlite3.OperationalError("disk failure")

    with pytest.raises(sqlite3.OperationalError):
        store.accept(envelope(), fails, 1100, identity=IDENTITY)
    assert store.lookup_receipt("sender", "conversation-1") is None
    assert store.accept(envelope(), effect, 1200, identity=IDENTITY).disposition == "accepted"


def test_order_gap_does_not_block_another_conversation(store: ExecutorStore) -> None:
    with pytest.raises(PlannerError, match="Earlier"):
        store.accept(envelope(2), effect, 1100, identity=IDENTITY)
    store.accept(envelope(conversation="other"), effect, 1100, identity=IDENTITY)
    store.accept(envelope(), effect, 1100, identity=IDENTITY)
    store.accept(envelope(2), effect, 1100, identity=IDENTITY)


def test_digest_binds_kind_and_routing_identity(store: ExecutorStore) -> None:
    original = envelope()
    store.accept(original, effect, 1100, identity=IDENTITY)
    with pytest.raises(PlannerError, match="digest"):
        store.accept(
            replace(original, kind=EnvelopeKind.backend_event), effect, 1200, identity=IDENTITY
        )
    with pytest.raises(PlannerError, match="digest"):
        store.accept(replace(original, conversation_id="another"), effect, 1200, identity=IDENTITY)


def test_stale_generation_cannot_write_after_replacement_and_old_close_is_harmless(
    store: ExecutorStore,
) -> None:
    replacement = replace(IDENTITY, connection_generation=2)
    store.begin_connection(replacement, 1100)
    with pytest.raises(PlannerError, match="stale"):
        store.accept(envelope(), effect, 1100, identity=IDENTITY)
    store.mark_lost(IDENTITY, 1200)
    store.accept(envelope(), effect, 1200, identity=replacement)
    store.mark_lost(replacement, 1300)
    store.mark_lost(replacement, 1400)
    connection = connect(store.db_path)
    try:
        assert (
            connection.execute("SELECT lost_at_ms FROM executor_connections").fetchone()[0] == 1300
        )
    finally:
        connection.close()


def test_first_answer_after_loss_is_durable_without_current_generation_or_readiness(
    store: ExecutorStore,
) -> None:
    ready_ask(store)
    store.mark_lost(IDENTITY, 1100)
    with pytest.raises(PlannerError, match="option"):
        store.choose_answer(ASK, "not-an-option", 999, 1102)
    answer = store.choose_answer(ASK, "allow", 999, 1103)
    assert answer.accepted_at_vps_ms == 1103
    assert store.choose_answer(ASK, "allow", 1, 1110) == answer
    assert not store.answer_is_deliverable(answer, IDENTITY)
    with pytest.raises(PlannerError, match="already"):
        store.choose_answer(ASK, "deny", 1, 1110)


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
    store: ExecutorStore,
    closure: str,
) -> None:
    ready_ask(store)
    if closure == "assignment":
        store.close_child(CHILD, 1100)
    elif closure in {"ended", "source_retired", "delivery_uncertain"}:
        persist_pending_turn(store, ASK, runtime_overrides={closure: True})
    elif closure == "pending":
        persist_pending_turn(store, ASK, runtime_overrides={"pending_permission_ask_ids": []})
    elif closure == "child":
        persist_pending_turn(store, replace(ASK, child=replace(CHILD, child_id="replacement")))
    elif closure == "turn":
        persist_pending_turn(store, replace(ASK, turn=TurnToken("conversation", 2)))
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
        store.choose_answer(ASK, "allow", 1, 1200)
    assert store.recorded_answer(ASK) is None


def test_identical_answer_receipt_survives_closure_and_application(store: ExecutorStore) -> None:
    ready_ask(store)
    answer = store.choose_answer(ASK, "allow", 1, 1100)
    store.close_child(CHILD, 1101)
    assert not store.answer_is_deliverable(answer, IDENTITY)
    store.record_application(AnswerApplication(answer.answer_id, ASK, 1102), identity=IDENTITY)
    assert store.choose_answer(ASK, "allow", 2, 9999) == answer
    with pytest.raises(PlannerError, match="already"):
        store.choose_answer(ASK, "deny", 2, 9999)
    assert not store.answer_is_deliverable(answer, IDENTITY)


def test_concurrent_first_choices_commit_one_original_receipt(store: ExecutorStore) -> None:
    ready_ask(store)
    store.mark_lost(IDENTITY, 1100)

    barrier = Barrier(2)

    def choose(option: str) -> object:
        barrier.wait(timeout=2)
        try:
            return store.choose_answer(ASK, option, 1, 1200)
        except PlannerError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(choose, ("allow", "deny")))
    assert sum(isinstance(result, PlannerError) for result in outcomes) == 1
    answer = store.recorded_answer(ASK)
    assert answer is not None and answer in outcomes
    assert store.choose_answer(ASK, answer.option_id or "", 1, 9999) == answer


@pytest.mark.parametrize("terminal", [AskReachability.child_gone, AskReachability.callback_gone])
def test_terminal_evidence_survives_loss_reconnect_reconcile_and_late_observation(
    store: ExecutorStore,
    terminal: AskReachability,
) -> None:
    ready_ask(store)
    store.reconcile(
        inventory()
        if terminal == AskReachability.child_gone
        else inventory(ChildInventory(CHILD, ASK.turn, (), ())),
        1100,
    )
    store.mark_lost(IDENTITY, 1101)
    replacement = replace(IDENTITY, connection_generation=2)
    store.begin_connection(replacement, 1200)
    store.reconcile(
        inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ()), identity=replacement), 1201
    )
    connection = connect(store.db_path)
    try:
        store.observe_callback_in_transaction(connection, ASK, replacement, 1202)
    finally:
        connection.close()
    evidence = store.ask_evidence(ASK)
    assert evidence is not None and evidence.reachability == terminal
    with pytest.raises(PlannerError):
        store.choose_answer(ASK, "allow", 2, 1203)


def test_old_durable_answer_reconciles_once_and_retains_application(
    store: ExecutorStore,
) -> None:
    ready_ask(store)
    answer = store.choose_answer(ASK, "allow", 1, 1100)
    store.mark_lost(IDENTITY, 1101)
    replacement = replace(IDENTITY, connection_generation=2)
    store.begin_connection(replacement, 3600000)
    result = store.reconcile(
        inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ()), identity=replacement), 3600001
    )
    assert result.answers == (answer,)
    assert result.ask_evidence[0].reachability == AskReachability.reachable
    store.record_application(
        AnswerApplication(answer.answer_id, ASK, 3600110), identity=replacement
    )
    assert store.reconcile(inventory(identity=replacement), 3600300).answers == ()
    evidence = store.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.applied_at_mac_ms == 3600110


def test_missing_child_and_missing_callback_publish_distinct_evidence(store: ExecutorStore) -> None:
    ready_ask(store)
    store.reconcile(inventory(ChildInventory(CHILD, ASK.turn, (), ())), 1200)
    evidence = store.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.reachability == AskReachability.callback_gone
    store.reconcile(inventory(), 1300)
    evidence = store.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.reachability == AskReachability.callback_gone
    with pytest.raises(PlannerError, match="closed or unknown"):
        store.choose_answer(ASK, "allow", 1, 1400)


def manifest(publication_id: str, contents: bytes) -> FileManifest:
    return FileManifest(
        publication_id,
        FileScope.ticket,
        "ticket",
        "result.bin",
        hashlib.sha256(contents).hexdigest(),
        len(contents),
    )


def test_quarantine_publication_old_retry_cannot_overwrite_newer(
    store: ExecutorStore,
    tmp_path: Path,
) -> None:
    receiver = PublicationReceiver(store, tmp_path / "files", tmp_path / "quarantine")
    old, new = manifest("old", b"old\x00"), manifest("new", b"new\xff")
    for entry, contents in ((old, b"old\x00"), (new, b"new\xff")):
        sealed = tmp_path / (entry.publication_id + ".sealed")
        sealed.write_bytes(contents)
        receiver.ingest_staged(entry, sealed, identity=IDENTITY)
        receiver.complete(entry, 1100, identity=IDENTITY)
    receiver.complete(old, 1200, identity=IDENTITY)
    assert (tmp_path / "files/tickets/ticket/result.bin").read_bytes() == b"new\xff"


def test_staged_publication_blocks_newer_until_rename_crash_recovery(
    store: ExecutorStore,
    tmp_path: Path,
) -> None:
    old, new = manifest("old", b"old"), manifest("new", b"new")
    receiver = PublicationReceiver(store, tmp_path / "files", tmp_path / "quarantine")
    sealed = tmp_path / "old.sealed"
    sealed.write_bytes(b"old")
    receiver.ingest_staged(old, sealed, identity=IDENTITY)

    def crash_after_rename() -> None:
        target = tmp_path / "files/tickets/ticket/result.bin"
        target.parent.mkdir(parents=True)
        (tmp_path / "quarantine/old.partial").replace(target)
        raise OSError("power loss after rename")

    with pytest.raises(OSError):
        store.accept_publication(old, crash_after_rename, 1100, identity=IDENTITY)
    with pytest.raises(PlannerError, match="recovery"):
        receiver.begin(new, identity=IDENTITY)
    assert not store.publication_accepted(old)
    receiver.complete(old, 1200, identity=IDENTITY)
    assert store.publication_accepted(old)
    receiver.begin(new, identity=IDENTITY)


def test_partial_corrupt_and_unsafe_files_never_publish(
    store: ExecutorStore, tmp_path: Path
) -> None:
    receiver = PublicationReceiver(store, tmp_path / "files", tmp_path / "quarantine")
    entry = manifest("publication", b"complete")
    receiver.begin(entry, identity=IDENTITY)
    sealed = tmp_path / "partial.sealed"
    sealed.write_bytes(b"part")
    with pytest.raises(ValueError, match="incomplete"):
        receiver.ingest_staged(entry, sealed, identity=IDENTITY)
    with pytest.raises(ValueError, match="incomplete"):
        receiver.complete(entry, 1100, identity=IDENTITY)
    assert not (tmp_path / "files/tickets/ticket/result.bin").exists()
    with pytest.raises(ValueError, match="unsafe"):
        receiver.begin(replace(entry, relative_path="../escape"), identity=IDENTITY)


def test_input_transfer_preserves_conversation_order_without_blocking_other_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bulk input must not block a command for an independent conversation."""
    import asyncio
    from uuid import uuid4

    from planner.core.clock import RealClock
    from planner.executor.contracts import ChildCommand, ChildOperation
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint(str(tmp_path / "unused.sqlite"), RealClock())
        admitted: list[str] = []
        started, release = asyncio.Event(), asyncio.Event()

        async def transfer(command: ChildCommand) -> None:
            admitted.append(command.command_id)
            if command.command_id == "bulk":
                started.set()
                await release.wait()

        monkeypatch.setattr(endpoint, "_transfer_command_inputs_locked", transfer)
        child = ChildReference(str(uuid4()), str(uuid4()), "conversation-a")
        bulk = ChildCommand("bulk", child, ChildOperation.stop, None, {})
        later = replace(bulk, command_id="later")
        unrelated = replace(
            bulk, command_id="unrelated", child=replace(child, conversation_id="conversation-b")
        )
        first = asyncio.create_task(endpoint._transfer_command_inputs(bulk))
        await asyncio.wait_for(started.wait(), 1)
        second = asyncio.create_task(endpoint._transfer_command_inputs(later))
        try:
            await asyncio.wait_for(endpoint._transfer_command_inputs(unrelated), 1)
            assert admitted == ["bulk", "unrelated"]
            assert not second.done()
            release.set()
            await asyncio.gather(first, second)
            assert admitted == ["bulk", "unrelated", "later"]
        finally:
            release.set()
            first.cancel()
            second.cancel()
            await asyncio.gather(first, second, return_exceptions=True)

    asyncio.run(run())


def test_session_file_cleanup_failure_still_releases_transport_root(
    store: ExecutorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import os
    import tempfile
    from typing import cast
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from planner.core.clock import RealClock
    from planner.executor.contracts import (
        ConnectionState,
        ExecutorHealth,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.mac import as_object
    from planner.executor.transfers import GenerationTransfers
    from planner.executor.vps import ExecutorEndpoint
    from planner.executor.wire import encode_frame

    async def run() -> None:
        endpoint = ExecutorEndpoint(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.store = store
        identity = replace(IDENTITY, executor_incarnation=str(uuid4()))
        reader = asyncio.StreamReader()
        reader.feed_data(
            encode_frame(
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

        async def setup_generation() -> None:
            endpoint._transports[identity] = cast(GenerationTransfers, service)
            endpoint._transport_roots[identity] = root
            endpoint._upload_transfers[(identity, "publication")] = "token"

        writer = Mock()
        writer.drain = AsyncMock(side_effect=setup_generation)
        monkeypatch.setattr(endpoint, "_measure_clock", AsyncMock())
        try:
            with pytest.raises(OSError, match="service cleanup failed"):
                await endpoint._session(reader, writer)
            service.close.assert_awaited_once()
            assert not root_path.exists()
            assert not endpoint._transports and not endpoint._transport_roots
            assert not endpoint._upload_transfers
            assert endpoint.writer is None and not endpoint.ready
        finally:
            root.cleanup()
            for task in tuple(endpoint._tasks):
                task.cancel()
            await asyncio.gather(*endpoint._tasks, return_exceptions=True)

    asyncio.run(run())


def test_receiver_copy_is_separate_from_mutable_staging_handle(
    store: ExecutorStore, tmp_path: Path
) -> None:
    """Ordinary account access retains handles. Publication owns a fresh inode."""
    import os

    receiver = PublicationReceiver(store, tmp_path / "files", tmp_path / "quarantine")
    staged = tmp_path / "staged"
    contents = b"verified original bytes"
    staged.write_bytes(contents)
    entry = manifest("fresh_receiver", contents)
    with staged.open("r+b", buffering=0) as retained:
        receiver.ingest_staged(entry, staged, identity=IDENTITY)
        receiver.complete(entry, 1100, identity=IDENTITY)
        target = tmp_path / "files/tickets/ticket/result.bin"
        assert target.stat().st_ino != os.fstat(retained.fileno()).st_ino
        retained.seek(0)
        retained.write(b"mutated after acceptance")
        retained.truncate()
        staged.unlink()
        assert target.read_bytes() == contents
        assert store.publication_accepted(entry)


@pytest.mark.parametrize("cancel_cleanup", [False, True])
def test_receiver_cancellation_drains_ingestion_before_transfer_cleanup(
    store: ExecutorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel_cleanup: bool
) -> None:
    import asyncio
    import tempfile
    import threading
    from unittest.mock import Mock

    from planner.core.clock import RealClock
    from planner.executor.contracts import FilePublicationRequest, WireMessage, WireMessageKind
    from planner.executor.mac import as_object
    from planner.executor.transfers import GenerationTransfers
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.store = store
        endpoint.identity = IDENTITY
        root = tempfile.TemporaryDirectory(dir=tmp_path)
        root_path = Path(root.name)
        service = GenerationTransfers(IDENTITY, root_path / "transfers", lambda: True)
        endpoint._transports[IDENTITY] = service
        endpoint._transport_roots[IDENTITY] = root
        receiver = PublicationReceiver(store, tmp_path / "files", tmp_path / "quarantine")
        endpoint.file_receiver = receiver
        contents = b"receiver copy source"
        entry = manifest("drain_receiver", contents)
        publication = FilePublicationRequest(entry, CHILD)
        monkeypatch.setattr(endpoint, "_authorize_file", Mock())
        payload, _ = await endpoint._file(
            WireMessage(WireMessageKind.file_manifest, "begin", as_object(publication)), IDENTITY
        )
        transfer_id = str(payload["transfer_id"])

        async def chunks() -> AsyncIterator[bytes]:
            yield contents

        await service.upload(transfer_id, chunks())
        staged = service.root / transfer_id
        entered, settle = threading.Event(), threading.Event()
        ingest = receiver.ingest_staged

        def delayed(*args: object, **kwargs: object) -> None:
            entered.set()
            assert settle.wait(3), "test did not release ingestion"
            assert staged.exists(), "generation source deleted during admitted work"
            ingest(entry, staged, identity=IDENTITY)

        monkeypatch.setattr(receiver, "ingest_staged", delayed)
        completion = asyncio.create_task(
            endpoint._file(
                WireMessage(WireMessageKind.file_complete, "complete", as_object(publication)),
                IDENTITY,
            )
        )
        cleanup = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            completion.cancel()
            cleanup = asyncio.create_task(endpoint._close_transport(IDENTITY))
            # Let both cancellation and cleanup reach their admitted-work waits.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            if cancel_cleanup:
                cleanup.cancel()
                await asyncio.sleep(0)
            assert not completion.done() and not cleanup.done()
            assert staged.exists() and root_path.exists()
            settle.set()
            await asyncio.gather(completion, return_exceptions=True)
            await asyncio.gather(cleanup, return_exceptions=True)
            assert not root_path.exists()
            assert not endpoint._receiver_work
            assert not store.publication_accepted(entry)
        finally:
            settle.set()
            await asyncio.gather(completion, return_exceptions=True)
            if cleanup is not None:
                await asyncio.gather(cleanup, return_exceptions=True)
            root.cleanup()

    asyncio.run(run())


def test_publication_completion_retains_original_child_and_generation_authority(
    store: ExecutorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from unittest.mock import Mock

    from planner.core.clock import RealClock
    from planner.executor.contracts import FilePublicationRequest, WireMessage, WireMessageKind
    from planner.executor.mac import as_object
    from planner.executor.transfers import GenerationTransfers
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.store = store
        endpoint.identity = IDENTITY
        service = GenerationTransfers(IDENTITY, tmp_path / "transfers", lambda: True)
        endpoint._transports[IDENTITY] = service
        receiver = PublicationReceiver(store, tmp_path / "files", tmp_path / "quarantine")
        endpoint.file_receiver = receiver
        contents = b"authorized original child"
        entry = manifest("original_child", contents)
        publication = FilePublicationRequest(entry, CHILD)
        # Isolate transfer-record authority from the separate principal resolver.
        monkeypatch.setattr(endpoint, "_authorize_file", Mock())
        payload, _ = await endpoint._file(
            WireMessage(WireMessageKind.file_manifest, "begin", as_object(publication)), IDENTITY
        )
        transfer_id = str(payload["transfer_id"])

        async def chunks() -> AsyncIterator[bytes]:
            yield contents

        await service.upload(transfer_id, chunks())
        replacement = replace(publication, child=replace(CHILD, child_id="replacement"))
        for kind in (WireMessageKind.file_manifest, WireMessageKind.file_complete):
            with pytest.raises(ValueError, match="assignment changed"):
                await endpoint._file(WireMessage(kind, "changed", as_object(replacement)), IDENTITY)
        # Connection fencing remains authoritative even while a transfer record exists.
        store.begin_connection(replace(IDENTITY, connection_generation=2), 1100)
        with pytest.raises(PlannerError, match="generation"):
            await endpoint._file(
                WireMessage(WireMessageKind.file_complete, "stale", as_object(publication)),
                IDENTITY,
            )
        assert not store.publication_accepted(entry)
        assert not (tmp_path / "files/tickets/ticket/result.bin").exists()
        await service.close()

    asyncio.run(run())


@pytest.mark.parametrize("accumulated", [False, True])
def test_closed_original_child_reconciles_outcomes_without_restoring_authority(
    store: ExecutorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, accumulated: bool
) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from planner.core.clock import RealClock
    from planner.executor.contracts import (
        CommandDisposition,
        CommandOutcome,
        FilePublicationRequest,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.mac import as_object
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        ready_ask(store)
        outcome = CommandOutcome("original-command", CHILD, CommandDisposition.succeeded, {})
        connection = connect(store.db_path)
        try:
            store.record_command_outcome_in_transaction(connection, outcome)
        finally:
            connection.close()
        endpoint = ExecutorEndpoint(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.store = store
        endpoint.identity = IDENTITY
        writer = Mock()
        endpoint.writer = writer
        adopt = AsyncMock()
        endpoint.runtime = SimpleNamespace(system=SimpleNamespace(adopt_remote_child=adopt))
        send = AsyncMock()
        monkeypatch.setattr(endpoint, "_send", send)
        monkeypatch.setattr(endpoint, "_send_reconciliation", send)
        original_reconcile = store.reconcile
        observed: list[ReconciliationInventory] = []

        def observe(value: ReconciliationInventory, now_ms: int) -> object:
            observed.append(value)
            return original_reconcile(value, now_ms)

        monkeypatch.setattr(store, "reconcile", observe)
        child = ChildInventory(CHILD, ASK.turn, (ASK,), ())
        if accumulated:
            # A child accepted in an earlier batch can close before final merge.
            endpoint._inventory[CHILD.child_id] = child
        store.close_child(CHILD, 1100)
        batch = ReconciliationInventory(IDENTITY, () if accumulated else (child,), (), (outcome,))
        for value in (batch, inventory()):
            await endpoint._handle_message(
                WireMessage(WireMessageKind.reconcile_inventory, "reconcile", as_object(value)),
                IDENTITY,
                writer,
            )
        adopt.assert_not_awaited()
        assert len(observed) == 1 and observed[0].children == ()
        assert observed[0].retained_command_outcomes == (outcome,)
        replies = [call.args[0] for call in send.await_args_list]
        assert [reply.kind for reply in replies] == [
            WireMessageKind.reconcile_result,
            WireMessageKind.command_outcome_ack,
            WireMessageKind.reconcile_result,
        ]
        assert replies[1].payload == {"command_id": outcome.command_id}
        evidence = store.ask_evidence(ASK)
        assert evidence is not None and evidence.reachability is AskReachability.child_gone
        assert store.child_assignment(CHILD) is None
        with pytest.raises(PlannerError, match="no VPS assignment"):
            endpoint._assignment(CHILD)
        with pytest.raises(PlannerError, match="no VPS assignment"):
            endpoint._authorize_file(FilePublicationRequest(manifest("new-work", b"new"), CHILD))

    asyncio.run(run())


@pytest.mark.parametrize("location", ["child", "outcome"])
@pytest.mark.parametrize("changed", ["child", "conversation", "incarnation"])
def test_reconciliation_closed_child_exception_requires_exact_original_identity(
    store: ExecutorStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    location: str,
    changed: str,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from planner.core.clock import RealClock
    from planner.executor.contracts import (
        CommandDisposition,
        CommandOutcome,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.mac import as_object
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        store.close_child(CHILD, 1100)
        changed_child = {
            "child": replace(CHILD, child_id="different"),
            "conversation": replace(CHILD, conversation_id="different"),
            "incarnation": replace(CHILD, executor_incarnation="different"),
        }[changed]
        endpoint = ExecutorEndpoint(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.store = store
        endpoint.identity = IDENTITY
        writer = Mock()
        endpoint.writer = writer
        adopt = AsyncMock()
        endpoint.runtime = SimpleNamespace(system=SimpleNamespace(adopt_remote_child=adopt))
        send = AsyncMock()
        monkeypatch.setattr(endpoint, "_send", send)
        monkeypatch.setattr(endpoint, "_send_reconciliation", send)
        value = ReconciliationInventory(
            IDENTITY,
            (ChildInventory(changed_child, None, (), ()),) if location == "child" else (),
            (),
            (CommandOutcome("untrusted", changed_child, CommandDisposition.succeeded, {}),)
            if location == "outcome"
            else (),
        )
        await endpoint._handle_message(
            WireMessage(WireMessageKind.reconcile_inventory, "invalid", as_object(value)),
            IDENTITY,
            writer,
        )
        adopt.assert_not_awaited()
        assert send.await_count == 1
        assert send.await_args is not None
        assert send.await_args.args[0].kind is WireMessageKind.error
        assert not endpoint._inventory and not endpoint._retained_inventory
        assert store.child_assignment(CHILD) is None

    asyncio.run(run())


def test_closed_child_reconciliation_cannot_use_stale_authenticated_generation(
    store: ExecutorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from planner.core.clock import RealClock
    from planner.executor.contracts import WireMessage, WireMessageKind
    from planner.executor.mac import as_object
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        store.close_child(CHILD, 1100)
        endpoint = ExecutorEndpoint(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.store = store
        endpoint.identity = IDENTITY
        writer = Mock()
        endpoint.writer = writer
        send = AsyncMock()
        monkeypatch.setattr(endpoint, "_send", send)
        store.begin_connection(replace(IDENTITY, connection_generation=2), 1200)
        await endpoint._handle_message(
            WireMessage(
                WireMessageKind.reconcile_inventory,
                "stale",
                as_object(inventory(ChildInventory(CHILD, None, (), ()))),
            ),
            IDENTITY,
            writer,
        )
        # The durable fence also suppresses a stale validation response.
        assert send.await_count == 0
        assert not endpoint._inventory

    asyncio.run(run())


def test_stop_hints_wait_for_exact_original_canonical_receipt(
    store: ExecutorStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    import json
    from unittest.mock import AsyncMock, Mock

    from planner.core.clock import RealClock
    from planner.executor.codec import encode_value
    from planner.executor.contracts import (
        ChildCommand,
        ChildOperation,
        CommandDisposition,
        CommandOutcome,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.mac import as_object
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        command = ChildCommand("retire", CHILD, ChildOperation.stop, None, {})
        outcome = CommandOutcome(command.command_id, CHILD, CommandDisposition.succeeded, {})
        persist_pending_turn(
            store, ASK, runtime_overrides={"initial_commands": [encode_value(command)]}
        )
        endpoint = ExecutorEndpoint(store.db_path, RealClock())
        endpoint.store = store
        endpoint.identity = IDENTITY
        writer = Mock()
        endpoint.writer = writer
        future = asyncio.get_running_loop().create_future()
        endpoint.pending_commands[command.command_id] = command, future
        send = AsyncMock()
        monkeypatch.setattr(endpoint, "_send", send)
        monkeypatch.setattr(endpoint, "_send_reconciliation", send)
        connection = connect(store.db_path)
        try:
            store.record_command_outcome_in_transaction(connection, outcome)
        finally:
            connection.close()
        submission = "outcome_" + hashlib.sha256(command.command_id.encode()).hexdigest()
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
        assert not store.has_accepted_command_outcome(outcome)
        await endpoint._handle_message(
            WireMessage(WireMessageKind.command_outcome, command.command_id, as_object(outcome)),
            IDENTITY,
            writer,
        )
        assert not future.done() and send.await_count == 0
        for value in (replace(inventory(), retained_command_outcomes=(outcome,)), inventory()):
            await endpoint._handle_message(
                WireMessage(WireMessageKind.reconcile_inventory, "inventory", as_object(value)),
                IDENTITY,
                writer,
            )
        assert not future.done()
        assert all(
            call.args[0].kind is not WireMessageKind.command_outcome_ack
            for call in send.await_args_list
        )
        assert store.child_assignment(CHILD) is not None
        payload = cast(JsonObject, encode_value(outcome))
        canonical = QueuedEnvelope(
            "canonical-sender",
            CHILD.conversation_id,
            1,
            submission,
            EnvelopeKind.command_outcome,
            payload,
            envelope_payload_digest(
                payload,
                (),
                sender_id="canonical-sender",
                conversation_id=CHILD.conversation_id,
                sequence=1,
                submission_id=submission,
                kind=EnvelopeKind.command_outcome,
            ),
        )
        receipt = await endpoint.accept_envelope(canonical, IDENTITY)
        assert receipt.disposition is ReceiptDisposition.accepted
        assert future.done() and future.result() == outcome
        assert store.has_accepted_command_outcome(outcome)
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
        EnvelopeKind.command_outcome,
        EnvelopeKind.answer_application,
        EnvelopeKind.cli_write,
    ],
)
def test_refused_native_evidence_marker_survives_domain_rollback_and_metadata_turns(
    store: ExecutorStore, kind: EnvelopeKind
) -> None:
    import json
    from typing import cast

    from planner.conversation.storage import ConversationStore
    from planner.executor.codec import encode_value
    from planner.executor.contracts import (
        BackendEvent,
        BackendEventMethod,
        CommandDisposition,
        CommandOutcome,
    )

    value: object
    if kind is EnvelopeKind.backend_event:
        value = BackendEvent(CHILD, BackendEventMethod.turn_ended, ASK.turn, {})
    elif kind is EnvelopeKind.command_outcome:
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
    assert store.cleanup_evidence_uncertain(CHILD) is (kind is not EnvelopeKind.cli_write)
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
    assert store.cleanup_evidence_uncertain(CHILD) is (kind is not EnvelopeKind.cli_write)
    assert store.lookup_receipt(queued.sender_id, queued.submission_id) == receipt


def test_retirement_metadata_is_monotonic_and_stale_child_cannot_replace_fresh(
    store: ExecutorStore,
) -> None:
    import json

    from planner.conversation.storage import ConversationStore
    from planner.executor.codec import encode_value

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
    store.close_child(CHILD, 1200)
    store.register_child(fresh, "ticket", "test-ticket", 1201)
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


def test_inventory_handler_rechecks_assignment_after_retirement_wins_core_lock(
    tmp_db: sqlite3.Connection,
    tmp_path: Path,
    native_registry: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import Any, Literal, cast
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from tests.unit.test_executor_native_runtime import canonical_snapshot

    from planner.conversation.contracts import (
        ConversationBackendKey,
        ConversationStartRequest,
        PromptDeliveryStarted,
        WorkspacePathPolicy,
    )
    from planner.conversation.events import ConversationTurnEnding
    from planner.conversation.message_content import text_message_content
    from planner.conversation.message_files import ConversationMessageFiles
    from planner.conversation.storage import ConversationStore
    from planner.conversation.system import SqliteProcessConversationSystem
    from planner.core.clock import RealClock
    from planner.core.contracts import OWNER_PRINCIPAL
    from planner.executor.contracts import (
        ChildOperation,
        CommandOutcome,
        WireMessage,
        WireMessageKind,
    )
    from planner.executor.mac import as_object
    from planner.executor.remote import RemoteBackendChildFactory
    from planner.executor.vps import ExecutorEndpoint
    from planner.projects.data import create_project
    from planner.tickets import data as ticket_data
    from planner.tickets.contracts import TITLE_MAX_CHARS, TicketLocale

    async def run() -> None:
        registry, spool, native, _ = cast(Any, native_registry)
        path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
        endpoint = ExecutorEndpoint(path, RealClock())
        identity = replace(IDENTITY, executor_incarnation=registry.incarnation)
        endpoint.identity = identity
        endpoint.ready = True
        endpoint.writer = Mock()
        endpoint.writer.drain = AsyncMock()
        endpoint.store.begin_connection(identity, 1)
        monkeypatch.setattr(endpoint, "_send", AsyncMock())
        commands: list[ChildCommand] = []

        def assign(start: Any, sink: Any) -> ChildReference:
            reference = ChildReference(registry.incarnation, str(uuid4()), start.conversation_id)
            endpoint.store.register_child(reference, "ticket", "synthetic", 1)
            endpoint.sinks[reference.child_id] = sink
            endpoint.children[reference.child_id] = endpoint.recover_child(
                reference, initial_history_complete=True
            )
            return reference

        async def replay() -> None:
            for queued in spool.pending():
                spool.acknowledge(await endpoint.accept_envelope(queued, identity))

        async def dispatch(command: ChildCommand) -> CommandOutcome:
            commands.append(command)
            outcome = await registry.execute_command(command)
            assert outcome.disposition is not CommandDisposition.refused, repr(outcome)
            await replay()
            return cast(CommandOutcome, outcome)

        monkeypatch.setattr(endpoint, "send_command", dispatch)
        monkeypatch.setattr(endpoint, "_read_skill_snapshot", lambda start: canonical_snapshot())
        factory = RemoteBackendChildFactory(
            assign,
            dispatch,
            lambda start: canonical_snapshot(),
            retire_reference=endpoint.retire_child,
        )
        system = SqliteProcessConversationSystem(
            store=ConversationStore(path),
            backend_child_factories={key: factory for key in ConversationBackendKey},
            message_files=ConversationMessageFiles(path),
        )
        system._catalog_endpoint = endpoint
        endpoint.runtime = SimpleNamespace(system=system)
        conversation = "retirement-handler"

        async def reconcile(item: ChildInventory) -> None:
            await endpoint._handle_message(
                WireMessage(
                    WireMessageKind.reconcile_inventory,
                    "inventory",
                    as_object(ReconciliationInventory(identity, (item,), (), ())),
                ),
                identity,
                cast(Any, endpoint.writer),
            )

        async def complete() -> None:
            state = system._conversations[conversation]
            assert state.running_turn is not None and native.sink is not None
            await native.sink.turn_ended(
                state.running_turn.token,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            await replay()
            await system.wait_until_quiescent()

        class ObservedLock(asyncio.Lock):
            def __init__(self) -> None:
                super().__init__()
                self.waiters = 0
                self.retirement_waiting = asyncio.Event()
                self.adoption_waiting = asyncio.Event()

            async def acquire(self) -> Literal[True]:
                if self.locked():
                    self.waiters += 1
                    (self.retirement_waiting if self.waiters == 1 else self.adoption_waiting).set()
                return await super().acquire()

        try:
            await system.start_conversation(
                ConversationStartRequest(
                    conversation, "scripted", workspace_folder=tmp_path,
                    workspace_path_policy=WorkspacePathPolicy.native_absolute,
                )
            )
            # Canonical native destination precedes first admission and survives
            # child retirement; a non-Ticket conversation initially selects VPS.
            project = create_project(tmp_db, name="Retirement fixture", folder_path=tmp_path, now=1)
            ticket = ticket_data.create_ticket(
                tmp_db,
                title="Retirement race",
                worker_type="coding",
                principal=OWNER_PRINCIPAL,
                now=1,
                title_max_chars=TITLE_MAX_CHARS,
                locale=TicketLocale.macos,
                project_id=project.id,
                employee_backend="codex",
                employee_launch_model="scripted",
                selection_kind="concrete",
            )
            ticket_data.write_ticket_conversation_start(
                tmp_db, ticket.id, conversation_id=conversation,
                backend="codex", model="scripted", reasoning_effort=None, now=2,
            )
            first_delivery = await system.send(
                conversation, text_message_content("first"), sender_label="Test"
            )
            assert isinstance(first_delivery, PromptDeliveryStarted), repr(first_delivery)
            state = system._conversations[conversation]
            first = state.executor_child_reference
            assert first is not None
            active = next(
                item
                for item in registry.inventory(identity, include_pending=False).children
                if item.child == first
            )
            await reconcile(active)
            assert state.child is endpoint.children[first.child_id]
            await native.sink.vendor_session_cursor_rebound("same-cursor")
            await replay()
            await complete()
            idle = replace(active, active_turn=None)
            await reconcile(idle)
            assert state.child is endpoint.children[first.child_id]
            # Ordered shutdown proves the fresh-child comparison before the race.
            await system.kill(conversation)
            assert endpoint.store.child_assignment(first) is None
            assert isinstance(
                await system.send(
                    conversation, text_message_content("ordered resume"), sender_label="Test"
                ),
                PromptDeliveryStarted,
            )
            second = state.executor_child_reference
            assert second is not None and second != first
            assert state.initial_shutdown_task is None
            assert [c for c in commands if c.operation is ChildOperation.start][-1].arguments[
                "vendor_session_cursor"
            ] == "same-cursor"
            await complete()
            captured = next(
                item
                for item in registry.inventory(identity, include_pending=False).children
                if item.child == second
            )
            entered, release = asyncio.Event(), asyncio.Event()
            original_stop = native.stop

            async def stopped_at_native() -> None:
                entered.set()
                await release.wait()
                await original_stop()

            monkeypatch.setattr(native, "stop", stopped_at_native)
            state.lock = lock = ObservedLock()
            killing = asyncio.create_task(system.kill(conversation))
            await entered.wait()
            await lock.acquire()
            release.set()
            await lock.retirement_waiting.wait()
            # Retirement now owns the first lock waiter. The endpoint still sees
            # an open assignment before adoption queues behind that same lock.
            assert endpoint.store.child_assignment(second) is not None
            adopting = asyncio.create_task(reconcile(captured))
            await lock.adoption_waiting.wait()
            assert not adopting.done()
            lock.release()
            await killing
            await adopting
            monkeypatch.setattr(native, "stop", original_stop)
            assert state.child is None and state.executor_child_reference is None
            assert endpoint.store.child_assignment(second) is None
            assert second.child_id not in registry.children
            assert second.child_id not in endpoint.children
            assert second.child_id not in endpoint.sinks
            assert second.child_id not in endpoint._inventory
            assert isinstance(
                await system.send(
                    conversation, text_message_content("raced resume"), sender_label="Test"
                ),
                PromptDeliveryStarted,
            )
            assert state.executor_child_reference not in (first, second)
            assert [c for c in commands if c.operation is ChildOperation.start][-1].arguments[
                "vendor_session_cursor"
            ] == "same-cursor"
        finally:
            await system.shutdown()

    asyncio.run(asyncio.wait_for(run(), 20))


def test_postcommit_error_preserves_durable_effect_and_receipt(
    store: ExecutorStore, monkeypatch: pytest.MonkeyPatch
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


def test_preflight_never_establishes_acceptance(store: ExecutorStore) -> None:
    assert store.preflight(envelope(), identity=IDENTITY) is None
    assert store.lookup_receipt("sender", "conversation-1") is None
    receipt = store.accept(envelope(), effect, 1100, identity=IDENTITY)
    assert receipt == store.lookup_receipt("sender", "conversation-1")


def test_control_frame_progresses_while_envelope_waits(tmp_path: Path) -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import Mock

    from tests.unit.test_executor_request_deadlines import peer

    from planner.core.clock import RealClock
    from planner.executor.codec import encode_value
    from planner.executor.contracts import AcceptanceReceipt, WireMessage, WireMessageKind
    from planner.executor.vps import ExecutorEndpoint

    async def exercise() -> None:
        endpoint = ExecutorEndpoint(str(tmp_path / "unused.sqlite"), RealClock())
        writer = peer()
        endpoint.identity, endpoint.writer = IDENTITY, writer
        endpoint.store = Mock()
        endpoint.store.unapplied_answers.return_value = ()
        endpoint.runtime = SimpleNamespace(
            system=SimpleNamespace()
        )
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

        endpoint.accept_envelope = accept  # type: ignore[method-assign]
        task = asyncio.create_task(endpoint._handle_message(frame, IDENTITY, writer))
        await entered.wait()
        await endpoint._handle_message(control, IDENTITY, writer)
        assert not task.done()
        assert endpoint.ready
        release.set()
        await task
        writer.close.assert_not_called()
        assert writer.write.call_count == 2

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
    from planner.executor import vps
    from planner.executor.contracts import WireMessage, WireMessageKind

    async def exercise() -> None:
        endpoint = vps.ExecutorEndpoint(str(tmp_path / "unused.sqlite"), RealClock())
        original, replacement = peer(), peer()
        endpoint.store = Mock()
        endpoint._measure_clock = AsyncMock()  # type: ignore[method-assign]
        endpoint._close_transport = AsyncMock()  # type: ignore[method-assign]
        endpoint._handle = AsyncMock()  # type: ignore[method-assign]
        calls = 0
        frame = WireMessage(WireMessageKind.error, "superseded", {})

        async def read(reader: Any) -> WireMessage:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _hello()
            endpoint.writer = replacement
            return frame

        monkeypatch.setattr(vps, "read_frame", read)
        await endpoint._session(Mock(), original)
        endpoint._handle.assert_not_called()
        replacement.close.assert_not_called()
        original.close.assert_called_once_with()
        await asyncio.gather(*endpoint._tasks)

    asyncio.run(exercise())


def historical_inventory_case(
    store: ExecutorStore,
    tmp_path: Path,
    *,
    operation: str = "stop",
    receipt: str = "accepted",
    historical: bool = True,
) -> tuple[ExecutorEndpoint, Any, ExecutorIdentity, ChildCommand, CommandOutcome]:
    """Exact stored command/result and canonical receipt with a real framed writer."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.unit.test_executor_request_deadlines import peer

    from planner.core.clock import RealClock
    from planner.executor.codec import encode_value
    from planner.executor.mac import as_object
    from planner.executor.vps import ExecutorEndpoint

    command = ChildCommand("historical-command", CHILD, ChildOperation(operation), ASK.turn, {})
    outcome = CommandOutcome(
        command.command_id, CHILD, CommandDisposition.refused, {"reason": "incarnation_gone"}
    )
    persist_pending_turn(
        store, ASK, runtime_overrides={"initial_commands": [encode_value(command)]}
    )
    connection = connect(store.db_path)
    try:
        store.record_command_outcome_in_transaction(connection, outcome)
    finally:
        connection.close()
    payload = as_object(outcome)
    if receipt == "digest":
        payload = as_object(replace(outcome, result={"different": True}))
    submission = "outcome_" + hashlib.sha256(command.command_id.encode()).hexdigest()
    canonical = QueuedEnvelope(
        "canonical-sender",
        CHILD.conversation_id,
        1,
        submission,
        EnvelopeKind.command_outcome,
        payload,
        envelope_payload_digest(
            payload,
            (),
            sender_id="canonical-sender",
            conversation_id=CHILD.conversation_id,
            sequence=1,
            submission_id=submission,
            kind=EnvelopeKind.command_outcome,
        ),
    )

    def publish(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        if receipt == "refused":
            raise PlannerError(ErrorCode.stale_claim, "refused evidence")
        return {}

    if receipt != "missing":
        store.accept(canonical, publish, 1100, identity=IDENTITY)
    identity = replace(
        IDENTITY,
        executor_incarnation="replacement" if historical else IDENTITY.executor_incarnation,
        connection_generation=2,
    )
    store.begin_connection(identity, 1200)
    endpoint = ExecutorEndpoint(store.db_path, RealClock())
    endpoint.store = store
    endpoint.identity = identity
    writer = peer()
    endpoint.writer = writer
    endpoint.runtime = SimpleNamespace(system=SimpleNamespace(adopt_remote_child=AsyncMock()))
    return endpoint, writer, identity, command, outcome


def captured_inventory_frames(writer: Any) -> list[WireMessage]:
    from pydantic import TypeAdapter

    return [
        TypeAdapter(WireMessage).validate_json(call.args[0][4:], strict=True)
        for call in writer.write.call_args_list
    ]


async def submit_inventory(
    endpoint: ExecutorEndpoint,
    writer: Any,
    identity: ExecutorIdentity,
    *outcomes: CommandOutcome,
    children: tuple[ChildInventory, ...] = (),
) -> None:
    from planner.executor.mac import as_object

    await endpoint._handle_message(
        WireMessage(
            WireMessageKind.reconcile_inventory,
            "inventory",
            as_object(ReconciliationInventory(identity, children, (), outcomes)),
        ),
        identity,
        writer,
    )


def historical_authority_rows(store: ExecutorStore) -> tuple[list[Any], ...]:
    connection = connect(store.db_path)
    try:
        return tuple(
            [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in (
                "executor_core_turns",
                "executor_child_assignments",
                "executor_command_outcomes",
            )
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "operation,closed", [("start", False), ("write_prompt", False), ("stop", False), ("stop", True)]
)
def test_historical_inventory_confirms_evidence_without_settling_callers(
    store: ExecutorStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    closed: bool,
) -> None:
    import asyncio
    from unittest.mock import Mock

    async def run() -> None:
        endpoint, writer, identity, command, outcome = historical_inventory_case(
            store, tmp_path, operation=operation
        )
        if closed:
            store.close_child(CHILD, 1300)
        baseline = historical_authority_rows(store)
        future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        endpoint.pending_commands[command.command_id] = command, future
        resolve = Mock(wraps=endpoint._resolve_command)
        monkeypatch.setattr(endpoint, "_resolve_command", resolve)
        await submit_inventory(endpoint, writer, identity, outcome)
        assert endpoint._retained_inventory == {outcome.command_id: outcome}
        await submit_inventory(endpoint, writer, identity)
        frames = captured_inventory_frames(writer)
        assert [frame.kind for frame in frames] == [
            WireMessageKind.reconcile_result,
            WireMessageKind.command_outcome_ack,
            WireMessageKind.reconcile_result,
        ]
        assert frames[1].payload == {"command_id": command.command_id}
        assert not endpoint._inventory and not endpoint._retained_inventory
        assert not future.done()
        resolve.assert_not_called()
        endpoint.runtime.system.adopt_remote_child.assert_not_awaited()
        assert not endpoint.children and not endpoint.sinks
        assert historical_authority_rows(store) == baseline

    asyncio.run(run())


@pytest.mark.parametrize(
    "changed",
    [
        "metadata",
        "command",
        "child",
        "conversation",
        "incarnation",
        "result",
        "missing",
        "refused",
        "digest",
    ],
)
def test_historical_inventory_requires_exact_command_result_and_accepted_receipt(
    store: ExecutorStore,
    tmp_path: Path,
    changed: str,
) -> None:
    import asyncio

    async def run() -> None:
        endpoint, writer, identity, command, outcome = historical_inventory_case(
            store,
            tmp_path,
            receipt=changed if changed in ("missing", "refused", "digest") else "accepted",
        )
        if changed == "metadata":
            connection = connect(store.db_path)
            try:
                connection.execute("UPDATE executor_core_turns SET runtime_json='{}'")
            finally:
                connection.close()
        elif changed == "command":
            outcome = replace(outcome, command_id="unknown")
        elif changed == "child":
            outcome = replace(outcome, child=replace(CHILD, child_id="unknown"))
        elif changed == "conversation":
            outcome = replace(outcome, child=replace(CHILD, conversation_id="unknown"))
        elif changed == "incarnation":
            outcome = replace(outcome, child=replace(CHILD, executor_incarnation="unknown"))
        elif changed == "result":
            outcome = replace(outcome, result={"different": True})
        baseline = historical_authority_rows(store)
        future: asyncio.Future[CommandOutcome] = asyncio.get_running_loop().create_future()
        endpoint.pending_commands[command.command_id] = command, future
        await submit_inventory(endpoint, writer, identity, outcome)
        frames = captured_inventory_frames(writer)
        assert len(frames) == 1 and frames[0].kind is WireMessageKind.error
        reason = str(frames[0].payload)
        assert (
            "retained command metadata"
            if changed in ("metadata", "command", "child", "conversation", "incarnation")
            else "accepted canonical receipt"
        ) in reason
        assert not endpoint._retained_inventory and not endpoint._inventory
        assert not future.done() and not endpoint.children
        endpoint.runtime.system.adopt_remote_child.assert_not_awaited()
        assert historical_authority_rows(store) == baseline

    asyncio.run(run())


@pytest.mark.parametrize("historical", [False, True])
@pytest.mark.parametrize("conflict", ["result", "child"])
def test_inventory_chunks_preserve_first_outcome_before_rejecting_conflicts(
    store: ExecutorStore,
    tmp_path: Path,
    historical: bool,
    conflict: str,
) -> None:
    import asyncio

    async def run() -> None:
        endpoint, writer, identity, _, outcome = historical_inventory_case(
            store, tmp_path, historical=historical
        )
        baseline = historical_authority_rows(store)
        await submit_inventory(endpoint, writer, identity, outcome)
        await submit_inventory(endpoint, writer, identity, outcome)
        assert endpoint._retained_inventory == {outcome.command_id: outcome}
        # Conflict wins even if the changed child would independently fail admission.
        conflicting = (
            replace(outcome, child=replace(CHILD, conversation_id="untrusted"))
            if conflict == "child"
            else replace(outcome, result={"different": True})
        )
        await submit_inventory(endpoint, writer, identity, conflicting)
        assert captured_inventory_frames(writer)[-1].kind is WireMessageKind.error
        assert "conflicting retained" in str(captured_inventory_frames(writer)[-1].payload)
        assert endpoint._retained_inventory == {outcome.command_id: outcome}
        await submit_inventory(endpoint, writer, identity)
        assert (
            sum(
                frame.kind is WireMessageKind.command_outcome_ack
                for frame in captured_inventory_frames(writer)
            )
            == 1
        )
        assert historical_authority_rows(store) == baseline

    asyncio.run(run())


@pytest.mark.parametrize("phase", ["adopt", "lock", "drain", "response"])
@pytest.mark.parametrize("replacement", ["writer", "durable"])
def test_historical_inventory_stops_stale_handlers_without_touching_replacement_aggregate(
    store: ExecutorStore,
    tmp_path: Path,
    phase: str,
    replacement: str,
) -> None:
    import asyncio

    from tests.unit.test_executor_request_deadlines import peer

    async def run() -> None:
        endpoint, writer, identity, _, outcome = historical_inventory_case(store, tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def pause(*args: Any) -> None:
            entered.set()
            await release.wait()

        if phase == "adopt":
            current = replace(
                CHILD, executor_incarnation=identity.executor_incarnation, child_id="live"
            )
            store.register_child(current, "ticket", "test-ticket", 1300)
            endpoint.runtime.system.adopt_remote_child.side_effect = pause
            task = asyncio.create_task(
                submit_inventory(
                    endpoint,
                    writer,
                    identity,
                    outcome,
                    children=(ChildInventory(current, None, (), ()),),
                )
            )
            await entered.wait()
        else:
            if phase != "response":
                await submit_inventory(endpoint, writer, identity, outcome)
                writer.write.reset_mock()
            if phase in ("lock", "response"):
                await endpoint._send_lock.acquire()
            else:
                writer.drain.side_effect = pause
            task = asyncio.create_task(
                submit_inventory(
                    endpoint, writer, identity, *((outcome,) if phase == "response" else ())
                )
            )
            if phase in ("lock", "response"):
                await asyncio.sleep(0)
                assert not task.done()
                if phase == "lock":
                    # Database checks now yield before the finalizer's ACK lock.
                    # Wait for detachment while that lock is still held.
                    async with asyncio.timeout(2):
                        while endpoint._retained_inventory or endpoint._inventory:
                            assert not task.done()
                            await asyncio.sleep(0)
                    assert not endpoint._retained_inventory and not endpoint._inventory
            else:
                await entered.wait()
        next_identity = replace(identity, connection_generation=identity.connection_generation + 1)
        store.begin_connection(next_identity, 1400)
        next_writer = peer()
        if replacement == "writer":
            endpoint.writer, endpoint.identity = next_writer, next_identity
        marker = replace(outcome, command_id="replacement-evidence")
        endpoint._retained_inventory = {marker.command_id: marker}
        marker_child = ChildInventory(CHILD, ASK.turn, (), ())
        endpoint._inventory = {CHILD.child_id: marker_child}
        if phase in ("lock", "response"):
            endpoint._send_lock.release()
        else:
            release.set()
        await task
        assert endpoint._retained_inventory == {marker.command_id: marker}
        assert endpoint._inventory == {CHILD.child_id: marker_child}
        next_writer.write.assert_not_called()
        frames = captured_inventory_frames(writer)
        assert len(frames) == (1 if phase == "drain" else 0)
        if frames:
            assert frames[0].kind is WireMessageKind.command_outcome_ack

    asyncio.run(run())
