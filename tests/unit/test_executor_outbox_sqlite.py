"""Crash/loss acceptance for the durable outbox, using SQLite and a scripted VPS.

These replace layout/pin/preflight assertions. Distinct risks are durable add,
atomic receipt cleanup, stable IDs, ordering/concurrency and wakeup races. No real
provider, browser or extra connected host is needed to prove these boundaries.
"""

from __future__ import annotations

import asyncio
import errno
import os
import sqlite3
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest

from planner.execution_configuration import SPOOL_FORMAT_VERSION
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import MacFilesApi, MacHttpClient
from planner.executor.mac_files import UploadRefused
from planner.executor.mac_outbox import DATABASE_NAME, MacOutbox, OutboxEntry
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileUploadRequest,
    ItemBatch,
    ItemBatchReply,
    ItemBatchStop,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
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
from planner.executor.mac_outbox import OutboxEntry
from planner.executor.protocol import EnvelopeKind
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


def test_unsupported_format_preserved_without_conversion(tmp_path: Path) -> None:
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


type Answer = Callable[[str, ItemBatch], Awaitable[ItemBatchReply]]


class ScriptedItems:
    """Route 4 of a scripted VPS, and an upload client that records what it was sent."""

    def __init__(self, answer: Answer, *, refuse_uploads: bool = False) -> None:
        self.answer = answer
        self.batches: list[ItemBatch] = []
        self.uploads: list[FileUploadRequest] = []
        self.refuse_uploads = refuse_uploads

    async def post_items(self, conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
        self.batches.append(batch)
        assert {item.envelope.conversation_id for item in batch.items} == {conversation_id}
        return await self.answer(conversation_id, batch)

    async def upload(self, upload: FileUploadRequest, source: Path) -> None:
        self.uploads.append(upload)
        assert source.read_bytes()
        if self.refuse_uploads:
            raise UploadRefused("artifact refused for this agent")


def accepted(batch: ItemBatch, result: JsonObject | None = None) -> ItemBatchReply:
    return ItemBatchReply(
        tuple(
            AcceptanceReceipt(
                item.envelope.sender_id,
                item.envelope.submission_id,
                ReceiptDisposition.refused
                if item.sender_refusal is not None
                else ReceiptDisposition.accepted,
                {"original": True} if result is None else result,
                1,
            )
            for item in batch.items
        )
    )


def busy(batch: ItemBatch) -> ItemBatchReply:
    stop = ItemBatchStop(batch.items[0].envelope.sequence, "retry_later", "VPS busy")
    return ItemBatchReply((), stop)


async def sending(outbox: MacOutbox, vps: ScriptedItems) -> asyncio.Task[None]:
    task = asyncio.create_task(
        outbox.send_pending(cast(MacHttpClient, vps), cast(MacFilesApi, vps))
    )
    await asyncio.sleep(0)
    return task


async def stop_sending(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_send_wakes_after_commit_and_stuck_conversations_hold_back_no_other(
    tmp_path: Path,
) -> None:
    # 7 November: four conversations whose items never got a reply filled every
    # send slot, so no other conversation's items were sent.
    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        active: set[str] = set()
        release = asyncio.Event()
        stuck = ("c_a", "c_b", "c_c", "c_d")

        async def answer(conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
            # The row is readable from another connection before sending starts.
            with sqlite3.connect(outbox.root / DATABASE_NAME) as connection:
                assert connection.execute(
                    "SELECT 1 FROM items WHERE submission_id=?",
                    (batch.items[0].envelope.submission_id,),
                ).fetchone()
            assert conversation_id not in active
            active.add(conversation_id)
            if conversation_id in stuck:
                await release.wait()
            active.remove(conversation_id)
            return accepted(batch)

        vps = ScriptedItems(answer)
        sender = await sending(outbox, vps)
        try:
            for conversation in (*stuck, "c_e"):
                for number in range(2):
                    await asyncio.to_thread(
                        outbox.add, entry(f"{conversation}_{number}", conversation)
                    )
            assert await outbox.wait_confirmed("c_e_1", 2) is not None
            sent = [item.envelope for batch in vps.batches for item in batch.items]
            assert {e.submission_id for e in sent if e.conversation_id in stuck} == {
                f"{conversation}_0" for conversation in stuck
            }
            release.set()
            for conversation in stuck:
                assert await outbox.wait_confirmed(f"{conversation}_1", 2) is not None
            sent = [item.envelope for batch in vps.batches for item in batch.items]
            for conversation in (*stuck, "c_e"):
                assert [e.submission_id for e in sent if e.conversation_id == conversation] == [
                    f"{conversation}_0",
                    f"{conversation}_1",
                ]
            assert outbox.pending_count() == 0
        finally:
            release.set()
            await stop_sending(sender)
            outbox.close()

    asyncio.run(run())


def test_failing_conversation_backs_off_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("planner.executor.mac_outbox.OUTBOX_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr("planner.executor.mac_outbox.OUTBOX_RETRY_MAX_SECONDS", 0.02)

    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        attempts: list[tuple[str, float]] = []
        others_confirmed = asyncio.Event()

        async def answer(conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
            if conversation_id == "c_bad":
                head = batch.items[0].envelope.submission_id
                attempts.append((head, asyncio.get_running_loop().time()))
                # Refuse until every other item is confirmed, and at least four times.
                if not others_confirmed.is_set() or len(attempts) < 5:
                    return busy(batch)
            return accepted(batch)

        for submission, conversation in (
            ("bad_0", "c_bad"),
            ("bad_1", "c_bad"),
            ("good_0", "c_good"),
            ("good_1", "c_good"),
            ("good_2", "c_good"),
        ):
            outbox.add(entry(submission, conversation))
        sender = await sending(outbox, ScriptedItems(answer))
        try:
            for submission in ("good_0", "good_1", "good_2"):
                assert await outbox.wait_confirmed(submission, 10) is not None
            others_confirmed.set()
            assert await outbox.wait_confirmed("bad_1", 10) is not None
            assert not sender.done()
            # The failing head was retried in place, never skipped, then the next sent.
            names = [name for name, _ in attempts]
            assert names[-1] == "bad_1" and set(names[:-1]) == {"bad_0"}
            # Each retry waited the first delay; no busy loop. "Retry later" does not grow
            # the delay, because the VPS already took its own time limit to answer.
            gaps = [b - a for (_, a), (_, b) in zip(attempts[:-2], attempts[1:-1], strict=True)]
            assert all(gap >= 0.009 for gap in gaps)
            failures = [r.getMessage() for r in caplog.records if "outbox send failed" in r.message]
            assert len(failures) == len(names) - 2
            assert "conversation=c_bad" in failures[0]
            assert failures[0].endswith("error=retry_later")
            assert outbox.pending_count() == 0
        finally:
            await stop_sending(sender)
            outbox.close()

    asyncio.run(run())


def test_batch_ends_after_the_first_item_that_is_not_a_plain_event(tmp_path: Path) -> None:
    """Risk: a cli_write in the middle of a batch gets the synthetic receipt a resend
    returns for already-stored plain events, and the agent reads {} as its result."""

    async def run() -> None:
        outbox = MacOutbox(tmp_path / "outbox")
        for number in range(3):
            outbox.add(entry(f"event_{number}", kind=EnvelopeKind.backend_event))
        outbox.add(entry("write"))
        for number in range(3, 5):
            outbox.add(entry(f"event_{number}", kind=EnvelopeKind.backend_event))

        async def answer(conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
            return accepted(batch, {"written": True})

        vps = ScriptedItems(answer)
        sender = await sending(outbox, vps)
        try:
            receipt = await outbox.wait_confirmed("write", 2)
            assert receipt is not None and receipt.result == {"written": True}
            while outbox.pending_count():
                await asyncio.sleep(0.01)
            assert [[i.envelope.submission_id for i in b.items] for b in vps.batches] == [
                ["event_0", "event_1", "event_2", "write"],
                ["event_3", "event_4"],
            ]
        finally:
            await stop_sending(sender)
            outbox.close()

    asyncio.run(run())


@pytest.mark.parametrize("refuse_uploads", [False, True])
def test_missing_files_upload_then_resend_and_a_refused_upload_rides_on_its_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refuse_uploads: bool
) -> None:
    """Risk: an item whose file the VPS lacks is resent forever, a VPS that lost an upload
    is never sent it again, or a file the VPS refuses for good blocks its conversation
    instead of becoming the item's refused receipt."""
    monkeypatch.setattr("planner.executor.mac_outbox.OUTBOX_RETRY_INITIAL_SECONDS", 0.01)

    async def run() -> None:
        outbox = MacOutbox(tmp_path / "executor/spool")
        artifacts = ExecutorPaths(tmp_path / "executor").artifacts / "t_one"
        artifacts.mkdir(parents=True)
        (artifacts / "notes.md").write_text("artifact bytes")
        child: JsonObject = {
            "executor_incarnation": str(uuid4()),
            "child_id": "child",
            "conversation_id": "c",
        }
        envelope = outbox.add(
            OutboxEntry("c", EnvelopeKind.backend_event, {"child": child}, "with_file"),
            ticket_id="t_one",
        )
        (manifest,) = envelope.file_dependencies

        async def answer(conversation_id: str, batch: ItemBatch) -> ItemBatchReply:
            (item,) = batch.items
            # Accepting needs two uploads: staging lost the first, as on a VPS restart.
            if item.sender_refusal is None and len(vps.uploads) < 2:
                stop = ItemBatchStop(
                    item.envelope.sequence, "missing_files", "send it", (manifest,)
                )
                return ItemBatchReply((), stop)
            return accepted(batch)

        vps = ScriptedItems(answer, refuse_uploads=refuse_uploads)
        sender = await sending(outbox, vps)
        try:
            receipt = await outbox.wait_confirmed("with_file", 2)
            assert receipt is not None
            assert vps.uploads[0].child.child_id == "child"
            refusals = [item.sender_refusal for batch in vps.batches for item in batch.items]
            if refuse_uploads:
                assert [upload.manifest for upload in vps.uploads] == [manifest]
                assert receipt.disposition is ReceiptDisposition.refused
                assert refusals == [None, "artifact refused for this agent"]
            else:
                # Asked again for a file it was sent, the sender backs off, then uploads it
                # again rather than stalling the conversation.
                assert [upload.manifest for upload in vps.uploads] == [manifest, manifest]
                assert receipt.disposition is ReceiptDisposition.accepted
                assert refusals == [None, None, None, None]
        finally:
            await stop_sending(sender)
            outbox.close()

    asyncio.run(run())


def test_add_returns_the_item_already_held_under_its_submission_id(tmp_path: Path) -> None:
    """Risk: publishing a command's owed output again after a crash hits the unique
    submission constraint, so the output is never marked published."""
    outbox = MacOutbox(tmp_path / "outbox")
    first = outbox.add(entry("owed", kind=EnvelopeKind.child_stopped))
    assert outbox.add(entry("owed", kind=EnvelopeKind.child_stopped)) == first
    assert outbox.pending_count() == 1
    outbox.close()


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
    for index in range(2):
        path = root / str(index)
        path.write_bytes(b"old")
        os.utime(path, ns=(old_ns, old_ns))
    outbox = MacOutbox(tmp_path / "spool")
    first = outbox.add(entry("first"), ticket_id="t_one")
    assert len(first.file_dependencies) == 2
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
