"""Crash/loss acceptance for the durable outbox, using SQLite and a scripted link.

These replace layout/pin/preflight assertions. Distinct risks are durable add,
atomic receipt cleanup, stable IDs, ordering/concurrency and wakeup races. No real
provider, browser or extra connected host is needed to prove these boundaries.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from planner.executor.configuration import SPOOL_FORMAT_VERSION
from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileScope,
    FileSnapshot,
    JsonObject,
    OutboxEntry,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
)
from planner.executor.files import snapshot_file
from planner.executor.mac_outbox import DATABASE_NAME, MacOutbox


def entry(
    identity: str = "write",
    conversation: str = "c_one",
    *,
    kind: EnvelopeKind = EnvelopeKind.cli_write,
) -> OutboxEntry:
    return OutboxEntry(conversation, kind, {"child": {"child_id": "child"}, "n": 1}, identity)


def receipt(
    envelope: QueuedEnvelope, disposition: ReceiptDisposition = ReceiptDisposition.accepted
) -> AcceptanceReceipt:
    return AcceptanceReceipt(
        envelope.sender_id,
        envelope.submission_id,
        envelope.payload_sha256,
        disposition,
        {"original": True},
        1,
    )


def test_stable_payload_global_sequence_and_confirmed_cleanup(tmp_path: Path) -> None:
    outbox = MacOutbox(tmp_path / "outbox")
    first = outbox.add(entry())
    assert outbox.add(entry()) == first
    with pytest.raises(ValueError, match="different content"):
        outbox.add(OutboxEntry("c_one", EnvelopeKind.cli_write, {"n": 2}, "write"))
    other = outbox.add(entry("other", "c_two"))
    assert other.sequence == first.sequence + 1
    outbox.acknowledge(receipt(first))
    assert outbox.lookup("write").receipt == receipt(first)  # type: ignore[union-attr]
    stop = outbox.add(entry("stop", kind=EnvelopeKind.child_stopped))
    outbox.acknowledge(receipt(stop))
    assert outbox.lookup("write") is None and outbox.lookup("stop") is None
    outbox.acknowledge(receipt(other))
    sender = outbox.sender_id
    outbox.close()
    reopened = MacOutbox(tmp_path / "outbox")
    assert reopened.sender_id == sender and reopened.lookup("other") is None
    assert reopened.add(entry("next")).sequence > stop.sequence
    reopened.close()


@pytest.mark.parametrize("commit", [False, True])
def test_process_crash_before_or_after_add_commit(tmp_path: Path, commit: bool) -> None:
    # A process exit, rather than close(), exercises SQLite's actual crash recovery.
    root = tmp_path / "outbox"
    script = """
import os, sys
from pathlib import Path
from planner.executor.mac_outbox import MacOutbox
from planner.executor.contracts import OutboxEntry, EnvelopeKind
box = MacOutbox(Path(sys.argv[1]))
if sys.argv[2] == 'False':
    box._db.execute("BEGIN IMMEDIATE")
    box._db.execute(
        "INSERT INTO items(submission_id,conversation_id,child_id,kind,payload_json) "
        "VALUES('uncommitted','c_one','','cli_write',?)", (b'{"payload":{},"files":[]}',)
    )
else:
    box.add(OutboxEntry('c_one', EnvelopeKind.cli_write, {}, 'committed'))
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(root), str(commit)], check=True)
    outbox = MacOutbox(root)
    assert [row.submission_id for row in outbox.pending()] == (["committed"] if commit else [])
    outbox.close()


def test_lost_receipt_retains_same_item_and_blob_until_confirmed(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"first bytes")
    snap = snapshot_file(
        root=source, relative_path="file", scope=FileScope.ticket, owner_id="t_one"
    )
    outbox = MacOutbox(tmp_path / "outbox")
    first = outbox.add(OutboxEntry("c_one", EnvelopeKind.backend_event, {}, "event", (snap,)))
    blob = outbox.snapshots("event")[0].local_path
    assert blob.read_bytes() == b"first bytes"
    outbox.close()  # Received confirmation was lost before the Mac persisted it.
    reopened = MacOutbox(tmp_path / "outbox")
    assert reopened.pending() == (first,) and blob.read_bytes() == b"first bytes"
    reopened.acknowledge(receipt(first))
    assert reopened.pending_count() == 0 and not blob.exists()
    reopened.close()


def test_add_failure_is_not_queued_and_orphan_blob_is_swept(tmp_path: Path) -> None:
    root = tmp_path / "outbox"
    outbox = MacOutbox(root)
    outbox._db.execute(
        "CREATE TRIGGER fail_add BEFORE INSERT ON items BEGIN SELECT RAISE(ABORT,'disk full'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="disk full"):
        outbox.add(entry())
    assert outbox.lookup("write") is None
    outbox._db.execute("DROP TRIGGER fail_add")
    outbox._db.commit()
    assert outbox.add(entry()).sequence == 1
    (root / "blobs" / ".tmp-orphan").write_bytes(b"partial")
    outbox.close()
    reopened = MacOutbox(root)
    assert list((root / "blobs").iterdir()) == []
    reopened.close()


@pytest.mark.parametrize("kind", [EnvelopeKind.cli_write, EnvelopeKind.backend_event])
def test_confirmation_commit_survives_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: EnvelopeKind
) -> None:
    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        row = outbox.add(entry(kind=kind))
        waiter = asyncio.create_task(outbox.wait_confirmed("write", 1))
        while not outbox._waiters:
            await asyncio.sleep(0)
        with monkeypatch.context() as patch:

            def fail_cleanup() -> None:
                raise OSError("cleanup failed")

            patch.setattr(outbox, "_sweep_blobs", fail_cleanup)
            with pytest.raises(OSError, match="cleanup failed"):
                await asyncio.to_thread(outbox.acknowledge, receipt(row))
        assert await waiter == receipt(row)
        retained = outbox.lookup("write")
        if kind is EnvelopeKind.cli_write:
            assert retained is not None and retained.receipt == receipt(row)
        else:
            assert retained is None
        assert outbox.pending_count() == 0
        outbox.close()
        reopened = MacOutbox(tmp_path / "outbox")
        assert reopened.lookup("write") is None
        reopened.close()

    asyncio.run(run())


def test_missing_snapshot_fails_add_and_new_database_gets_new_sender(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"bytes")
    snap = snapshot_file(
        root=source, relative_path="file", scope=FileScope.ticket, owner_id="t_one"
    )
    snap.local_path.unlink()
    root = tmp_path / "outbox"
    outbox = MacOutbox(root)
    sender = outbox.sender_id
    with pytest.raises(FileNotFoundError):
        outbox.add(OutboxEntry("c_one", EnvelopeKind.backend_event, {}, "missing", (snap,)))
    assert outbox.pending_count() == 0
    outbox.close()
    shutil.rmtree(root)
    replacement = MacOutbox(root)
    assert replacement.sender_id != sender
    replacement.close()


def test_previous_format_refused_without_conversion(tmp_path: Path) -> None:
    root = tmp_path / "outbox"
    root.mkdir()
    database = root / DATABASE_NAME
    with sqlite3.connect(database) as connection:
        connection.execute(f"PRAGMA user_version={SPOOL_FORMAT_VERSION - 1}")
    before = database.read_bytes()
    with pytest.raises(ValueError, match="unsupported outbox format"):
        MacOutbox(root)
    assert database.read_bytes() == before
    assert not (root / "blobs").exists()


def test_wal_full_and_one_process_owner(tmp_path: Path) -> None:
    outbox = MacOutbox(tmp_path / "outbox")
    assert outbox._db.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert outbox._db.execute("PRAGMA synchronous").fetchone() == (2,)
    assert outbox._db.execute("PRAGMA fullfsync").fetchone() == (1,)
    with pytest.raises(BlockingIOError):
        MacOutbox(tmp_path / "outbox")
    assert {
        row[0] for row in outbox._db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    } == {"sender", "items", "sqlite_sequence"}
    outbox.close()


def test_send_wakes_after_commit_four_conversations_and_one_each(tmp_path: Path) -> None:
    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        active: set[str] = set()
        sent: list[QueuedEnvelope] = []
        four_started = asyncio.Event()
        release = asyncio.Event()

        class Link:
            async def request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
                assert kind is WireMessageKind.envelope
                envelope = TypeAdapter(QueuedEnvelope).validate_python(payload)
                # The row is readable from another connection before sending starts.
                with sqlite3.connect(outbox.root / DATABASE_NAME) as connection:
                    assert connection.execute(
                        "SELECT 1 FROM items WHERE submission_id=?", (envelope.submission_id,)
                    ).fetchone()
                assert envelope.conversation_id not in active
                active.add(envelope.conversation_id)
                assert len(active) <= 4
                sent.append(envelope)
                if len(active) == 4:
                    four_started.set()
                await release.wait()
                active.remove(envelope.conversation_id)
                return WireMessage(
                    WireMessageKind.receipt,
                    "reply",
                    json.loads(TypeAdapter(AcceptanceReceipt).dump_json(receipt(envelope))),
                )

            async def publish_file(self, envelope: QueuedEnvelope, snapshot: FileSnapshot) -> None:
                raise AssertionError("no files")

        sender = asyncio.create_task(outbox.send_pending(Link()))
        await asyncio.sleep(0)
        try:
            for conversation in ("c_a", "c_b", "c_c", "c_d", "c_e"):
                for number in range(2):
                    await asyncio.to_thread(
                        outbox.add, entry(f"{conversation}_{number}", conversation)
                    )
            await asyncio.wait_for(four_started.wait(), 2)
            assert len(sent) == 4
            release.set()
            confirmed = await outbox.wait_confirmed("c_e_1", 2)
            assert confirmed is not None
            for conversation in ("c_a", "c_b", "c_c", "c_d", "c_e"):
                assert [e.submission_id for e in sent if e.conversation_id == conversation] == [
                    f"{conversation}_0",
                    f"{conversation}_1",
                ]
            assert outbox.pending_count() == 0
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            outbox.close()

    asyncio.run(run())


def test_refused_confirmation_is_available_to_later_retry_without_requeue(tmp_path: Path) -> None:
    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        row = outbox.add(entry())
        assert await outbox.wait_confirmed("write", 0) is None
        outbox.acknowledge(receipt(row, ReceiptDisposition.refused))
        assert await outbox.wait_confirmed("write", 0) == receipt(row, ReceiptDisposition.refused)
        assert outbox.add(entry()) == row
        assert outbox.pending_count() == 0
        outbox.close()

    asyncio.run(run())


def test_missing_files_upload_then_lost_receipt_replays_original_item(tmp_path: Path) -> None:
    async def run() -> None:
        source = tmp_path / "source"
        source.mkdir()
        (source / "file").write_bytes(b"original bytes")
        snapshot = snapshot_file(
            root=source, relative_path="file", scope=FileScope.ticket, owner_id="t_one"
        )
        outbox = MacOutbox(tmp_path / "outbox")
        envelope = outbox.add(
            OutboxEntry("c_one", EnvelopeKind.backend_event, {}, "event", (snapshot,))
        )
        uploaded = False
        accepted = False
        attempts = 0

        class Link:
            async def request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
                nonlocal attempts, accepted
                assert TypeAdapter(QueuedEnvelope).validate_python(payload) == envelope
                attempts += 1
                if not uploaded:
                    return WireMessage(
                        WireMessageKind.missing_files,
                        "missing",
                        {
                            "files": [
                                json.loads(
                                    TypeAdapter(type(snapshot.manifest)).dump_json(
                                        snapshot.manifest
                                    )
                                )
                            ]
                        },
                    )
                if not accepted:
                    accepted = True
                    raise ConnectionError("VPS committed but reply was lost")
                return WireMessage(
                    WireMessageKind.receipt,
                    "confirmed",
                    json.loads(TypeAdapter(AcceptanceReceipt).dump_json(receipt(envelope))),
                )

            async def publish_file(self, row: QueuedEnvelope, file: FileSnapshot) -> None:
                nonlocal uploaded
                assert not uploaded and file.local_path.read_bytes() == b"original bytes"
                uploaded = True

        with pytest.raises(ConnectionError, match="reply was lost"):
            await outbox.send_pending(Link())
        assert outbox.pending() == (envelope,)
        outbox.close()
        reopened = MacOutbox(tmp_path / "outbox")
        sender = asyncio.create_task(reopened.send_pending(Link()))
        try:
            # An event is deleted on confirmation, so wait before sending starts.
            assert await reopened.wait_confirmed("event", 2) == receipt(envelope)
            assert attempts == 3 and reopened.lookup("event") is None
            assert list((reopened.root / "blobs").iterdir()) == []
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            reopened.close()

    asyncio.run(run())


def test_busy_confirmation_retries_same_sequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import mac_outbox as module

    monkeypatch.setattr(module, "OUTBOX_BUSY_TIMEOUT_MS", 10)

    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        envelope = outbox.add(entry())
        busy = sqlite3.connect(outbox.root / DATABASE_NAME)
        busy.execute("BEGIN IMMEDIATE")
        attempts = 0
        retried = asyncio.Event()

        class Link:
            async def request(self, kind: WireMessageKind, payload: JsonObject) -> WireMessage:
                nonlocal attempts
                assert TypeAdapter(QueuedEnvelope).validate_python(payload) == envelope
                attempts += 1
                if attempts > 1:
                    retried.set()
                return WireMessage(
                    WireMessageKind.receipt,
                    "reply",
                    json.loads(TypeAdapter(AcceptanceReceipt).dump_json(receipt(envelope))),
                )

            async def publish_file(self, envelope: QueuedEnvelope, snapshot: FileSnapshot) -> None:
                raise AssertionError("no files")

        sender = asyncio.create_task(outbox.send_pending(Link()))
        try:
            await asyncio.wait_for(retried.wait(), 2)
            assert outbox.pending_count() == 1
            busy.rollback()
            assert await outbox.wait_confirmed("write", 2) == receipt(envelope)
            assert outbox.pending_count() == 0
        finally:
            busy.close()
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            outbox.close()

    asyncio.run(run())
