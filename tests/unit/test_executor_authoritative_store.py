"""Authoritative acceptance, durable choices, generation fencing and file recovery."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from planner.conversation.backends.contracts import TurnToken
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect
from planner.core.migrations.versions.executor_authoritative_acceptance import STATEMENTS
from planner.executor.configuration import PROTOCOL_VERSION
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
    FileChunk,
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

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, 1, "incarnation", 1)
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


def test_answer_selection_needs_exact_callback_current_generation_and_ready(
    store: ExecutorStore,
) -> None:
    store.publish_ask(ASK, ("allow", "deny"), 1000)
    with pytest.raises(PlannerError, match="unconfirmed"):
        store.choose_answer(ASK, "allow", 1, 1100)
    store.reconcile(inventory(ChildInventory(CHILD, ASK.turn, (ASK,), ())), 1100)
    with pytest.raises(PlannerError, match="unconfirmed"):
        store.choose_answer(ASK, "allow", 1, 1101)
    store.mark_ready(IDENTITY, 1102)
    answer = store.choose_answer(ASK, "allow", 1, 1103)
    assert store.choose_answer(ASK, "allow", 1, 1110) == answer
    with pytest.raises(PlannerError, match="already"):
        store.choose_answer(ASK, "deny", 1, 1110)


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
    assert evidence.reachability == AskReachability.child_gone
    with pytest.raises(PlannerError, match="unconfirmed"):
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
        receiver.begin(entry, identity=IDENTITY)
        receiver.write_chunk(
            FileChunk(entry.publication_id, 0, base64.b64encode(contents).decode()),
            identity=IDENTITY,
        )
        receiver.complete(entry, 1100, identity=IDENTITY)
    receiver.complete(old, 1200, identity=IDENTITY)
    assert (tmp_path / "files/tickets/ticket/result.bin").read_bytes() == b"new\xff"


def test_staged_publication_blocks_newer_until_rename_crash_recovery(
    store: ExecutorStore,
    tmp_path: Path,
) -> None:
    old, new = manifest("old", b"old"), manifest("new", b"new")
    receiver = PublicationReceiver(store, tmp_path / "files", tmp_path / "quarantine")
    receiver.begin(old, identity=IDENTITY)
    receiver.write_chunk(FileChunk("old", 0, base64.b64encode(b"old").decode()), identity=IDENTITY)

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
    receiver.write_chunk(
        FileChunk("publication", 0, base64.b64encode(b"part").decode()), identity=IDENTITY
    )
    with pytest.raises(ValueError, match="incomplete"):
        receiver.complete(entry, 1100, identity=IDENTITY)
    assert not (tmp_path / "files/tickets/ticket/result.bin").exists()
    with pytest.raises(ValueError, match="unsafe"):
        receiver.begin(replace(entry, relative_path="../escape"), identity=IDENTITY)
