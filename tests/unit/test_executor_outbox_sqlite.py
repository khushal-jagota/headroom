"""Crash/loss acceptance for the durable outbox, using SQLite and a scripted link.

These replace layout/pin/preflight assertions. Distinct risks are durable add,
atomic receipt cleanup, stable IDs, ordering/concurrency and wakeup races. No real
provider, browser or extra connected host is needed to prove these boundaries.
"""

from __future__ import annotations

import asyncio
import errno
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter
from tests.support.executor_control import deliver, message_link
from tests.unit.test_executor_request_deadlines import IDENTITY, peer

from planner.execution_configuration import SPOOL_FORMAT_VERSION
from planner.executor.mac_outbox import DATABASE_NAME, MacOutbox
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    OutboxEntry,
    QueuedEnvelope,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
)


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


@pytest.mark.parametrize("error", [errno.ENOSPC, errno.EACCES])
def test_capture_storage_errors_still_refuse_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: int
) -> None:
    box = MacOutbox(tmp_path / "spool")
    folder = tmp_path / "artifacts" / "tickets" / "t_one"
    folder.mkdir(parents=True)
    (folder / "file.txt").write_text("authored bytes")
    original_open = os.open

    def fail_blob(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if isinstance(path, Path) and path.name.startswith(".tmp-"):
            raise OSError(error, "cannot save captured bytes")
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_blob)
    try:
        with pytest.raises(OSError) as failure:
            box.add(entry(), ticket_id="t_one")
        assert failure.value.errno == error
        assert box.lookup("write") is None
    finally:
        box.close()


def test_stable_payload_global_sequence_and_confirmed_cleanup(tmp_path: Path) -> None:
    outbox = MacOutbox(tmp_path / "outbox")
    first = outbox.add(entry())
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
from planner.executor.protocol import OutboxEntry, EnvelopeKind
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
    source = tmp_path / "artifacts" / "tickets" / "t_one"
    source.mkdir(parents=True)
    (source / "file").write_bytes(b"first bytes")
    outbox = MacOutbox(tmp_path / "outbox")
    first = outbox.add(
        OutboxEntry("c_one", EnvelopeKind.backend_event, {}, "event"), ticket_id="t_one"
    )
    blob = outbox.snapshots(first)[0].local_path
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


def test_send_wakes_after_commit_four_conversations_and_one_each(tmp_path: Path) -> None:
    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        active: set[str] = set()
        sent: list[QueuedEnvelope] = []
        four_started = asyncio.Event()
        release = asyncio.Event()

        writer = peer()
        link = message_link(writer, IDENTITY)
        replies: list[asyncio.Task[None]] = []

        async def respond(message: WireMessage) -> None:
            assert message.kind is WireMessageKind.envelope
            envelope = TypeAdapter(QueuedEnvelope).validate_python(message.payload)
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
            await deliver(
                link,
                WireMessage(
                    WireMessageKind.receipt,
                    message.request_id,
                    json.loads(TypeAdapter(AcceptanceReceipt).dump_json(receipt(envelope))),
                ),
            )

        async def send(message: WireMessage) -> None:
            replies.append(asyncio.create_task(respond(message)))

        writer.send.side_effect = send
        sender = asyncio.create_task(outbox.send_pending(link))
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
            release.set()
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            await asyncio.gather(*replies)
            await link.wait_closed()
            outbox.close()

    asyncio.run(run())


def test_refused_confirmation_is_available_to_later_retry_without_requeue(tmp_path: Path) -> None:
    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        row = outbox.add(entry())
        assert await outbox.wait_confirmed("write", 0) is None
        outbox.acknowledge(receipt(row, ReceiptDisposition.refused))
        assert await outbox.wait_confirmed("write", 0) == receipt(row, ReceiptDisposition.refused)
        assert outbox.pending_count() == 0
        outbox.close()

    asyncio.run(run())


def test_ticket_index_skips_unchanged_sources_and_rechecks_racy_same_size_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gap: hashing every source or trusting a recent same-size stat loses the design."""
    import os
    import time
    from contextlib import contextmanager

    from planner.executor import mac_outbox as module

    root = tmp_path / "artifacts/tickets/t_one"
    root.mkdir(parents=True)
    old_ns = time.time_ns() - 10_000_000_000
    for index in range(200):
        path = root / str(index)
        path.write_bytes(b"old")
        os.utime(path, ns=(old_ns, old_ns))
    outbox = MacOutbox(tmp_path / "spool")
    first = outbox.add(entry("first"), ticket_id="t_one")
    assert len(first.file_dependencies) == 200
    outbox.acknowledge(receipt(first))
    opened: list[Path] = []
    from planner.executor.files import regular_descriptor

    original = regular_descriptor

    @contextmanager
    def observe(path: Path):  # type: ignore[no-untyped-def]
        if path.is_relative_to(root):
            opened.append(path)
        with original(path) as descriptor:
            yield descriptor

    monkeypatch.setattr(module, "regular_descriptor", observe)
    assert outbox.add(entry("unchanged"), ticket_id="t_one").file_dependencies == ()
    assert opened == []
    # Same-size change with exactly the prior timestamp, inside one clock tick.
    source = root / "0"
    recent = time.time_ns()
    source.write_bytes(b"one")
    os.utime(source, ns=(recent, recent))
    second = outbox.add(entry("second"), ticket_id="t_one")
    source.write_bytes(b"two")
    os.utime(source, ns=(recent, recent))
    third = outbox.add(entry("third"), ticket_id="t_one")
    assert len(opened) == 2 and set(opened) == {source}
    assert outbox.snapshots(second)[0].local_path.read_bytes() == b"one"
    assert outbox.snapshots(third)[0].local_path.read_bytes() == b"two"
    outbox.close()
    reopened = MacOutbox(tmp_path / "spool")
    assert reopened.snapshots(second)[0].local_path.read_bytes() == b"one"
    assert reopened.snapshots(third)[0].local_path.read_bytes() == b"two"
    reopened.close()


def test_later_write_retains_unchanged_bytes_when_earlier_capture_is_refused(
    tmp_path: Path,
) -> None:
    import os
    import time

    root = tmp_path / "artifacts/tickets/t_one"
    root.mkdir(parents=True)
    source = root / "file"
    source.write_bytes(b"shared bytes")
    old = time.time_ns() - 10_000_000_000
    os.utime(source, ns=(old, old))
    box = MacOutbox(tmp_path / "spool")
    first = box.add(entry("first"), ticket_id="t_one")
    later = box.add(entry("later"), ticket_id="t_one")
    assert later.file_dependencies == first.file_dependencies
    blob = box.snapshots(later)[0].local_path
    box.acknowledge(receipt(first, ReceiptDisposition.refused))
    assert blob.read_bytes() == b"shared bytes"
    assert box._db.execute("SELECT COUNT(*) FROM ticket_files").fetchone()[0] == 0
    box.acknowledge(receipt(later))
    assert not blob.exists()
    # A future write captures again after the refused index was removed.
    future = box.add(entry("future"), ticket_id="t_one")
    assert future.file_dependencies == first.file_dependencies
    box.close()


def test_refusal_during_recent_file_recheck_keeps_later_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same bytes rehashed during a refusal still belong to the later pending item."""
    from collections.abc import Iterator
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    from planner.executor.files import regular_descriptor

    root = tmp_path / "artifacts/tickets/t_one"
    root.mkdir(parents=True)
    source = root / "file"
    source.write_bytes(b"same bytes")
    box = MacOutbox(tmp_path / "spool")
    first = box.add(entry("first"), ticket_id="t_one")
    entered, release = Event(), Event()

    @contextmanager
    def held(path: Path) -> Iterator[int]:
        with regular_descriptor(path) as descriptor:
            if path == source:
                entered.set()
                assert release.wait(5)
            yield descriptor

    monkeypatch.setattr("planner.executor.mac_outbox.regular_descriptor", held)
    with ThreadPoolExecutor() as pool:
        later = pool.submit(box.add, entry("later"), ticket_id="t_one")
        assert entered.wait(2)
        box.acknowledge(receipt(first, ReceiptDisposition.refused))
        release.set()
        assert later.result().file_dependencies == first.file_dependencies
    assert box.snapshots(later.result())[0].local_path.read_bytes() == b"same bytes"
    box.close()
