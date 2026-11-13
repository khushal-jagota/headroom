"""Authoritative acceptance, durable choices, ordering and file recovery."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.executor_inbox import lookup_receipt
from tests.support.executor_presence import make_current
from tests.support.executor_turns import persist_pending_turn
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry

import planner.executor.vps_inbox as vps_inbox
from planner.conversation.backends.contracts import TurnToken
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity
from planner.executor.protocol import (
    EnvelopeKind,
    FileManifest,
    FileScope,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
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
        create_schema(connection)
        connection.execute("CREATE TABLE effects (id TEXT PRIMARY KEY)")
    finally:
        connection.close()
    result = InboxStore(path)
    make_current(path, IDENTITY)
    persist_pending_turn(result.runs, ASK)
    return result


def envelope(sequence: int = 1, *, conversation: str = "conversation") -> QueuedEnvelope:
    payload: JsonObject = {"operation": "test"}
    submission_id = f"{conversation}-{sequence}"
    return QueuedEnvelope(
        "sender", conversation, sequence, submission_id, EnvelopeKind.cli_write, payload
    )


def effect(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
    connection.execute("INSERT INTO effects VALUES (?)", (envelope.submission_id,))
    return {"first": envelope.submission_id}


def test_transient_storage_failure_has_no_receipt_and_retry_applies(store: InboxStore) -> None:
    def fails(connection: sqlite3.Connection, envelope: QueuedEnvelope) -> JsonObject:
        effect(connection, envelope)
        raise sqlite3.OperationalError("disk failure")

    with pytest.raises(sqlite3.OperationalError):
        store.accept(envelope(), fails, 1100, identity=IDENTITY)
    assert lookup_receipt(store, "sender", "conversation-1") is None
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


def manifest(contents: bytes) -> FileManifest:
    return FileManifest(
        FileScope.ticket,
        "test-ticket",
        "result.bin",
        hashlib.sha256(contents).hexdigest(),
        len(contents),
    )


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

    @contextmanager
    def failing_connection(path: str) -> Iterator[FailingCommit]:
        connection = original_connect(path)
        try:
            yield FailingCommit(connection)
        finally:
            connection.close()

    with monkeypatch.context() as failure:
        failure.setattr(store_module, "runtime_connection", failing_connection)
        with pytest.raises(RuntimeError) as raised:
            store.accept(row, effect, 123, identity=IDENTITY)
    assert raised.value is error
    assert calls == ["commit", "rollback"]
    saved = lookup_receipt(store, row.sender_id, row.submission_id)
    assert saved is not None and saved.disposition is ReceiptDisposition.accepted
    assert store.accept(row, effect, 124, identity=IDENTITY) == saved
    connection = connect(store.db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    finally:
        connection.close()


def file_envelope(sequence: int, file: FileManifest) -> QueuedEnvelope:
    from planner.executor.protocol import encode_value

    payload: JsonObject = {"child": encode_value(CHILD)}
    row = replace(
        envelope(sequence),
        kind=EnvelopeKind.backend_event,
        payload=payload,
        file_dependencies=(file,),
    )
    return row


def accept_with_files(
    store: InboxStore,
    row: QueuedEnvelope,
    writer: Any,
    now: int,
) -> Any:
    published = store.publish_file_dependencies(row, IDENTITY)
    return store.accept(row, writer, now, identity=IDENTITY, published=published)


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

        receipts.append(accept_with_files(store, row, write, 1))
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
        accept_with_files(store, row, crash, 1)
    assert store.preflight(row, identity=IDENTITY) is None
    assert accept_with_files(store, row, effect, 2).disposition is ReceiptDisposition.accepted


# Risk: Mac artifact acceptance holding SQLite's one writer while it reads, hashes or
# renames a large artifact set, so other writers exhaust their five-second wait. Gap: the
# tests above prove order, install-before-effect and crash recovery, but not which file
# work runs under the writer, the publication bound, or authority between groups. Layer:
# unit, the real InboxStore on a temp database; a second connection with no busy wait
# shows directly whether a writer is held.


def _probe_writer_free(store: InboxStore) -> bool:
    """True when another connection can commit right now, without waiting."""
    probe = sqlite3.connect(store.db_path, timeout=0, isolation_level=None)
    try:
        probe.execute("BEGIN IMMEDIATE")
        probe.execute("CREATE TABLE IF NOT EXISTS probe (n INTEGER)")
        probe.execute("INSERT INTO probe VALUES (1)")
        probe.execute("COMMIT")
        return True
    except sqlite3.OperationalError as error:
        assert "locked" in str(error)
        return False
    finally:
        probe.close()


def _uploaded(store: InboxStore, tmp_path: Path, relative_path: str, data: bytes) -> FileManifest:
    file = replace(manifest(data), relative_path=relative_path)
    source = tmp_path / "source"
    source.write_bytes(data)
    store.store_upload(file, source)
    return file


def _files_envelope(sequence: int, files: tuple[FileManifest, ...]) -> QueuedEnvelope:
    return replace(file_envelope(sequence, files[0]), file_dependencies=files)


def test_file_bytes_are_read_only_while_other_writers_can_commit(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New uploads and a repeated, already published dependency are each hashed outside
    any writer; the receipt's transaction reads no bytes."""
    from planner.executor import files as files_module

    store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
    first = _build(store, tmp_path, 3)
    original_hash = files_module._hash_descriptor
    hashed_while_free: list[bool] = []

    def hash_and_probe(descriptor: int) -> tuple[str, int]:
        hashed_while_free.append(_probe_writer_free(store))
        return original_hash(descriptor)

    monkeypatch.setattr(files_module, "_hash_descriptor", hash_and_probe)
    assert accept_with_files(store, _files_envelope(1, first), effect, 1).disposition == "accepted"
    # The outbox repeats an unchanged manifest on a later item: checked again, still free.
    repeated = (first[0], _uploaded(store, tmp_path, "build/new.bin", b"new"))
    assert accept_with_files(store, _files_envelope(2, repeated), effect, 2).disposition == (
        "accepted"
    )
    assert hashed_while_free and all(hashed_while_free)
    assert len(hashed_while_free) == 3 + 2  # once per dependency per item, no more
    assert _published(store, first) and store.file_target(repeated[1]).read_bytes() == b"new"


def _build(store: InboxStore, tmp_path: Path, count: int) -> tuple[FileManifest, ...]:
    """Uploaded files, each in its own directory, as a build output is."""
    return tuple(
        _uploaded(store, tmp_path, f"build/{n}/part.bin", bytes([n]) * 1000) for n in range(count)
    )


def _published(store: InboxStore, files: tuple[FileManifest, ...]) -> bool:
    return all(
        store.file_target(file).read_bytes() == bytes([n]) * 1000 for n, file in enumerate(files)
    )


class _Transactions:
    """Count the store's write transactions, the renames each one holds, and the path
    checks made under any of them."""

    def __init__(self, store: InboxStore, monkeypatch: pytest.MonkeyPatch) -> None:
        self.renames: list[int] = []
        self.paths_checked_under_writer = 0
        self.before: dict[int, Any] = {}
        self.renamed_under_writer: list[bool] = []
        self.open = False
        original = store._transaction
        original_replace = os.replace
        original_target = store.file_target

        @contextmanager
        def counted() -> Iterator[sqlite3.Connection]:
            hook = self.before.get(len(self.renames))
            if hook is not None:
                hook()
            self.renames.append(0)
            with original() as connection:
                self.open = True
                try:
                    yield connection
                finally:
                    self.open = False

        def replaced(*args: Any, **kwargs: Any) -> None:
            if self.open:
                self.renames[-1] += 1
                self.renamed_under_writer.append(not _probe_writer_free(store))
            original_replace(*args, **kwargs)

        def target(manifest: FileManifest) -> Path:
            self.paths_checked_under_writer += self.open
            return original_target(manifest)

        monkeypatch.setattr(store, "_transaction", counted)
        monkeypatch.setattr(store, "file_target", target)
        monkeypatch.setattr(os, "replace", replaced)


def test_many_files_publish_in_bounded_groups_each_under_the_writer(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
    files = _build(store, tmp_path, 5)
    monkeypatch.setattr(vps_inbox, "MAX_FILES_PER_PUBLICATION_TRANSACTION", 2)
    transactions = _Transactions(store, monkeypatch)
    receipt = accept_with_files(store, _files_envelope(1, files), effect, 1)
    assert receipt.disposition is ReceiptDisposition.accepted
    # Three publication transactions of at most two renames, then the receipt's, with none.
    assert transactions.renames == [2, 2, 1, 0]
    assert all(transactions.renamed_under_writer)
    # Each path is checked once, before any writer: a group's hold must not grow with
    # the whole item (1,511 checks held each group for about 0.5 s).
    assert transactions.paths_checked_under_writer == 0
    assert _published(store, files)


@pytest.mark.parametrize("revoked", ["assignment closed", "sender replaced"])
def test_authority_lost_between_groups_stops_later_renames_and_the_effect(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, revoked: str
) -> None:
    store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
    files = _build(store, tmp_path, 5)
    monkeypatch.setattr(vps_inbox, "MAX_FILES_PER_PUBLICATION_TRANSACTION", 2)
    transactions = _Transactions(store, monkeypatch)
    successor = replace(IDENTITY, executor_incarnation="successor")
    transactions.before[1] = (
        (lambda: store.runs.close_child(CHILD, 2))
        if revoked == "assignment closed"
        else (lambda: make_current(store.db_path, successor))
    )
    row = _files_envelope(1, files)
    with pytest.raises(PlannerError) as raised:
        accept_with_files(store, row, effect, 1)
    assert raised.value.code is (
        ErrorCode.agent_forbidden
        if revoked == "assignment closed"
        else ErrorCode.executor_incarnation_not_current
    )
    assert transactions.renames == [2, 0]
    published = [store.file_target(file).exists() for file in files]
    assert published == [True, True, False, False, False]  # earlier authorized files stay
    assert lookup_receipt(store, row.sender_id, row.submission_id) is None
    connection = connect(store.db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("changed", ["upload", "target"])
def test_a_file_changed_after_its_check_is_retried_never_accepted_unchecked(
    store: InboxStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    store.runs.register_child(CHILD, "ticket", "test-ticket", 1)
    file = _uploaded(store, tmp_path, "out/result.bin", b"exact")
    row = file_envelope(1, file)
    if changed == "upload":
        transactions = _Transactions(store, monkeypatch)
        # A new inode with the same name appears after the hash, before the rename.
        transactions.before[0] = lambda: store.store_upload(file, tmp_path / "source")
        with pytest.raises(PlannerError) as raised:
            accept_with_files(store, row, effect, 1)
        assert transactions.renames == [0]
        assert not store.file_target(file).exists()
    else:
        published = store.publish_file_dependencies(row, IDENTITY)
        store.file_target(file).write_bytes(b"other")  # e.g. a local process rewrote it
        with pytest.raises(PlannerError) as raised:
            store.accept(row, effect, 1, identity=IDENTITY, published=published)
    assert raised.value.code is ErrorCode.retry_later
    assert lookup_receipt(store, row.sender_id, row.submission_id) is None
    monkeypatch.undo()
    if changed == "target":
        # The resend checks again: the served bytes are wrong and the upload was used,
        # so the Mac uploads again before the item applies.
        with pytest.raises(vps_inbox.MissingFiles):
            store.publish_file_dependencies(row, IDENTITY)
        _uploaded(store, tmp_path, "out/result.bin", b"exact")
    receipt = accept_with_files(store, row, effect, 2)
    assert receipt.disposition is ReceiptDisposition.accepted
    assert store.file_target(file).read_bytes() == b"exact"
    assert store.accept(row, effect, 3, identity=IDENTITY) == receipt  # no file work
    connection = connect(store.db_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM effects").fetchone()[0] == 1
    finally:
        connection.close()
