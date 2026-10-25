"""Authoritative acceptance, durable choices, generation fencing and file recovery."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier

import pytest
from tests.support.executor_turns import persist_pending_turn

from planner.conversation.backends.contracts import TurnToken
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.core.migrations.versions.executor_authoritative_acceptance import STATEMENTS
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AnswerApplication,
    AskReachability,
    AskReference,
    AuthorizedAction,
    ChildInventory,
    ChildReference,
    ClockSample,
    EnvelopeKind,
    ExecutorIdentity,
    FileManifest,
    FileScope,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
    ReconciliationInventory,
)
from planner.executor.journal import envelope_payload_digest
from planner.executor.publication import PublicationReceiver
from planner.executor.store import ExecutorStore

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1)
CHILD = ChildReference("incarnation", "child", "conversation")
ASK = AskReference(CHILD, TurnToken("conversation", 1), "ask")


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
        "move_retirement_uncertain",
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
    elif closure in {"ended", "source_retired", "move_retirement_uncertain", "delivery_uncertain"}:
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


def test_old_durable_answer_reconciles_once_and_application_is_separate_from_action(
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
    unknown = store.action_timing(ASK)
    assert unknown is not None
    assert unknown.unknown_reason == "action not observed"
    sample = ClockSample(3600000, 3600105, 3600107, 3600012)
    store.record_action(
        AuthorizedAction(answer.answer_id, ASK, "tool", 3600200), sample, identity=replacement
    )
    timing = store.action_timing(ASK)
    assert timing is not None
    assert (timing.elapsed_lower_ms, timing.elapsed_upper_ms) == (3598995, 3599005)
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
        receiver.ingest_sealed(entry, sealed, identity=IDENTITY)
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
    receiver.ingest_sealed(old, sealed, identity=IDENTITY)

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
        receiver.ingest_sealed(entry, sealed, identity=IDENTITY)
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


def test_session_preview_cleanup_failure_still_releases_transport_root(
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
    from planner.executor.sftp import RestrictedSftpService
    from planner.executor.vps import ExecutorEndpoint
    from planner.executor.wire import encode_frame
    from planner.previews.forward import PreviewForwards

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
        previews = Mock()
        previews.close = AsyncMock(side_effect=ValueError("unsafe preview socket"))
        service = Mock()
        service.close = AsyncMock(side_effect=OSError("service cleanup failed"))
        root = tempfile.TemporaryDirectory(dir=tmp_path)
        root_path = Path(root.name)

        async def setup_generation() -> None:
            endpoint._preview_forwards = cast(PreviewForwards, previews)
            endpoint._transports[identity] = cast(RestrictedSftpService, service)
            endpoint._transport_roots[identity] = root
            endpoint._upload_slots[(identity, "publication")] = "token"

        writer = Mock()
        writer.drain = AsyncMock(side_effect=setup_generation)
        monkeypatch.setattr(endpoint, "_measure_clock", AsyncMock())
        try:
            with pytest.raises(OSError, match="service cleanup failed"):
                await endpoint._session(reader, writer)
            previews.close.assert_awaited_once()
            service.close.assert_awaited_once()
            assert not root_path.exists()
            assert not endpoint._transports and not endpoint._transport_roots
            assert not endpoint._upload_slots
            assert endpoint.writer is None and not endpoint.ready
        finally:
            root.cleanup()
            for task in tuple(endpoint._tasks):
                task.cancel()
            await asyncio.gather(*endpoint._tasks, return_exceptions=True)

    asyncio.run(run())
