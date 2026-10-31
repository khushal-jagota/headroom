"""Authoritative acceptance, durable choices, generation fencing and file recovery."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from tests.support.executor_control import MessageReader
from tests.support.executor_inbox import lookup_receipt

from planner.execution_identity import AskKind, AskReference, ChildReference, ExecutorIdentity

if TYPE_CHECKING:
    pass

import pytest
from tests.support.executor_turns import persist_pending_turn
from tests.unit.test_executor_native_runtime import isolated_native_home as isolated_native_home
from tests.unit.test_executor_native_runtime import native_registry as native_registry
from tests.unit.test_executor_request_deadlines import peer

from planner.conversation.backends.contracts import TurnToken
from planner.core.db import connect, create_schema
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.executor.protocol import (
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
        create_schema(connection)
        connection.execute("CREATE TABLE effects (id TEXT PRIMARY KEY)")
    finally:
        connection.close()
    result = InboxStore(path)
    result.runs.begin_connection(IDENTITY)
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
    from unittest.mock import AsyncMock, Mock
    from uuid import uuid4

    from planner.core.clock import RealClock
    from planner.executor.link_files import GenerationTransfers
    from planner.executor.protocol import (
        ConnectionState,
        ExecutorHealth,
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
            await endpoint.catalog.close()

    asyncio.run(run())


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
