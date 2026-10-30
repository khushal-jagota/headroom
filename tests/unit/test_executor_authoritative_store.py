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

from tests.support.executor_control import MessageReader

if TYPE_CHECKING:
    pass

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
) -> tuple[ExecutorIdentity, tuple[ChildInventory, ...]]:
    return identity, children


def ready_ask(store: ExecutorStore) -> None:
    store.publish_ask(ASK, ("allow", "deny"), 1000)
    store.observe_children(*inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ())), 1001)
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
    store.observe_children(
        *inventory()
        if terminal == AskReachability.child_gone
        else inventory(ChildInventory(CHILD, ASK.turn, (), ())),
        1100,
    )
    store.mark_lost(IDENTITY, 1101)
    replacement = replace(IDENTITY, connection_generation=2)
    store.begin_connection(replacement, 1200)
    store.observe_children(
        *inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ()), identity=replacement), 1201
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
    store.observe_children(
        *inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ()), identity=replacement), 3600001
    )
    assert store.unapplied_answers() == (answer,)
    assert store.ask_evidence(ASK).reachability == AskReachability.reachable  # type: ignore[union-attr]
    store.record_application(
        AnswerApplication(answer.answer_id, ASK, 3600110), identity=replacement
    )
    store.observe_children(*inventory(identity=replacement), 3600300)
    assert store.unapplied_answers() == ()
    evidence = store.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.applied_at_mac_ms == 3600110


def test_missing_child_and_missing_callback_publish_distinct_evidence(store: ExecutorStore) -> None:
    ready_ask(store)
    store.observe_children(*inventory(ChildInventory(CHILD, ASK.turn, (), ())), 1200)
    evidence = store.ask_evidence(ASK)
    assert evidence is not None
    assert evidence.reachability == AskReachability.callback_gone
    store.observe_children(*inventory(), 1300)
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
    from planner.executor.contracts import ChildCommand
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
    )
    from planner.executor.mac import as_object
    from planner.executor.transfers import GenerationTransfers
    from planner.executor.vps import ExecutorEndpoint
    from planner.executor.wire import encode_message

    async def run() -> None:
        endpoint = ExecutorEndpoint(str(tmp_path / "vps.sqlite"), RealClock())
        endpoint.store = store
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
            endpoint._upload_transfers[(identity, "publication")] = "token"

        writer = Mock()
        writer.send = AsyncMock(side_effect=setup_generation)
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
    from planner.executor.contracts import FilePublicationRequest, WireMessage
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
    from planner.executor.contracts import FilePublicationRequest, WireMessage
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
        WireMessage,
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
        assert not store.has_accepted_command_outcome(outcome)
        await endpoint._handle_message(
            WireMessage(WireMessageKind.command_outcome, command.command_id, as_object(outcome)),
            IDENTITY,
            writer,
        )
        assert not future.done() and send.await_count == 0
        await endpoint._resolve_command(outcome)
        assert not future.done()
        assert store.child_assignment(CHILD) is not None
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
        EnvelopeKind.child_stopped,
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
    )

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
    from unittest.mock import AsyncMock, Mock

    from tests.unit.test_executor_request_deadlines import peer

    from planner.core.clock import RealClock
    from planner.executor.codec import encode_value
    from planner.executor.contracts import AcceptanceReceipt, WireMessage
    from planner.executor.vps import ExecutorEndpoint

    async def exercise() -> None:
        endpoint = ExecutorEndpoint(str(tmp_path / "unused.sqlite"), RealClock())
        writer = peer()
        endpoint.identity, endpoint.writer = IDENTITY, writer
        endpoint.store = Mock()
        endpoint.store.unapplied_answers.return_value = ()
        from planner.executor.contracts import ReconnectResult
        from planner.executor.mac import as_object

        endpoint._request = AsyncMock(  # type: ignore[method-assign]
            return_value=WireMessage(
                WireMessageKind.reconnect_result,
                "ask",
                as_object(ReconnectResult(IDENTITY, (), ())),
            )
        )
        endpoint.runtime = SimpleNamespace(system=SimpleNamespace())
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
    from planner.executor import vps
    from planner.executor.contracts import WireMessage

    async def exercise() -> None:
        endpoint = vps.ExecutorEndpoint(str(tmp_path / "unused.sqlite"), RealClock())
        original, replacement = peer(), peer()
        endpoint.store = Mock()
        endpoint._measure_clock = AsyncMock()  # type: ignore[method-assign]
        endpoint._close_transport = AsyncMock()  # type: ignore[method-assign]
        endpoint._handle = AsyncMock()  # type: ignore[method-assign]
        calls = 0
        frame = WireMessage(WireMessageKind.error, "superseded", {})

        async def read() -> WireMessage:
            nonlocal calls
            calls += 1
            if calls == 1:
                return _hello()
            endpoint.writer = replacement
            return frame

        original.receive = AsyncMock(side_effect=read)
        await endpoint._session(original, original)
        endpoint._handle.assert_not_called()
        replacement.close.assert_not_called()
        original.close.assert_called_once_with()
        await asyncio.gather(*endpoint._tasks)

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid", [None, "generation", "child", "command_ids", "unfinished"])
def test_one_reconnect_ask_resolves_finished_and_keeps_inflight_commands(
    store: ExecutorStore, monkeypatch: pytest.MonkeyPatch, invalid: str | None
) -> None:
    """Gap: one snapshot replaces outcome ACK/chunk aggregation without granting stale authority."""
    import asyncio
    from unittest.mock import AsyncMock, Mock

    from planner.core.clock import RealClock
    from planner.executor.contracts import CommandState, ReconnectResult
    from planner.executor.mac import as_object
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint(store.db_path, RealClock())
        endpoint.identity, endpoint.writer = IDENTITY, Mock()
        commands = tuple(
            ChildCommand(phase, CHILD, ChildOperation.cancel_running_turn, None, {})
            for phase in ("unknown", "preparing", "invoking", "finished")
        )
        for command in commands:
            endpoint.pending_commands[command.command_id] = (
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
        monkeypatch.setattr(endpoint, "_request", request)
        if invalid is not None:
            with pytest.raises(ValueError):
                await endpoint._reconnect(IDENTITY, endpoint.writer)
            assert all(not f.done() for _, f in endpoint.pending_commands.values())
        else:
            assert await endpoint._reconnect(IDENTITY, endpoint.writer) == {"unknown"}
            assert endpoint.pending_commands["finished"][1].result() == outcome
            assert all(
                not endpoint.pending_commands[key][1].done()
                for key in ("unknown", "preparing", "invoking")
            )
        request.assert_awaited_once_with(
            WireMessageKind.reconnect_ask, {"command_ids": [c.command_id for c in commands]}
        )

    asyncio.run(run())


def test_ready_ack_includes_stop_registered_after_reconnect_ask(
    store: ExecutorStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: retirement can register while the ready ACK yields, after the ask snapshot."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from tests.unit.test_executor_request_deadlines import peer

    from planner.core.clock import RealClock
    from planner.executor.vps import ExecutorEndpoint

    async def run() -> None:
        endpoint = ExecutorEndpoint(store.db_path, RealClock())
        writer = peer()
        endpoint.identity, endpoint.writer = IDENTITY, writer
        endpoint.runtime = SimpleNamespace(system=SimpleNamespace())
        command = ChildCommand("late-stop", CHILD, ChildOperation.stop, None, {})
        caller: asyncio.Task[CommandOutcome] | None = None
        outcome = CommandOutcome(command.command_id, CHILD, CommandDisposition.uncertain, {})

        async def ready(message: WireMessage) -> None:
            nonlocal caller
            assert message.kind is WireMessageKind.reconciliation_ready
            assert not endpoint.ready
            caller = asyncio.create_task(endpoint.send_command(command))
            await asyncio.sleep(0)
            assert command.command_id in endpoint.pending_commands

        async def emit(*args: object) -> bool:
            assert endpoint.ready
            endpoint.pending_commands[command.command_id][1].set_result(outcome)
            return True

        writer.send.side_effect = ready
        monkeypatch.setattr(endpoint, "_reconnect", AsyncMock(return_value=set()))
        monkeypatch.setattr(endpoint, "_transfer_command_inputs", AsyncMock())
        emitted = AsyncMock(side_effect=emit)
        monkeypatch.setattr(endpoint, "_emit_command", emitted)
        try:
            await endpoint._handle_message(
                WireMessage(WireMessageKind.reconciliation_ready, "ready", {}), IDENTITY, writer
            )
            emitted.assert_awaited_once_with(command, IDENTITY, writer)
            assert caller is not None and await asyncio.wait_for(caller, 1) == outcome
        finally:
            if caller is not None and not caller.done():
                caller.cancel()
                await asyncio.gather(caller, return_exceptions=True)

    asyncio.run(run())
