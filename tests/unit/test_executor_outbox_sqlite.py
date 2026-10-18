"""SQLite atomicity, pinned transfer bytes, indexed heads, and offline conversion."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import TypeAdapter

from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileScope,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
)
from planner.executor.files import fsync_directory, snapshot_file
from planner.executor.journal import FilesystemSpool, envelope_payload_digest
from planner.executor.outbox_conversion import CONVERSION_DIRECTORY, convert_legacy_spool
from planner.executor.outbox_storage import DATABASE_NAME, publish_metadata, write_file
from planner.executor.preflight import check_spool_compatibility


def receipt(envelope: QueuedEnvelope) -> AcceptanceReceipt:
    return AcceptanceReceipt(
        envelope.sender_id,
        envelope.submission_id,
        envelope.payload_sha256,
        ReceiptDisposition.accepted,
        {},
        1,
    )


def contents(root: Path) -> dict[str, bytes | None]:
    return {
        str(p.relative_to(root)): None if p.is_dir() else p.read_bytes() for p in root.rglob("*")
    }


def test_indexed_heads_counts_and_permanent_sequence_ownership(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    rows = [
        spool.mint_envelope(conversation_id=c, kind=EnvelopeKind.cli_write, payload={"n": n})
        for c, n in (("c_b", 1), ("c_a", 1), ("c_b", 2), ("c_a", 2))
    ]
    assert spool.pending_heads() == (rows[1], rows[0])
    assert spool.pending_count() == 4
    assert spool.has_pending() and spool.has_pending("c_a")
    assert not spool.has_pending("c_absent")
    spool.acknowledge(receipt(rows[1]))
    assert spool.pending_heads() == (rows[3], rows[0])
    assert spool.pending_count() == 3
    with pytest.raises(ValueError, match="sequence conflicts"):
        old = rows[1]
        identity = "other-id"
        digest = envelope_payload_digest(
            old.payload,
            (),
            sender_id=old.sender_id,
            conversation_id=old.conversation_id,
            sequence=old.sequence,
            submission_id=identity,
            kind=old.kind,
        )
        spool.enqueue(
            QueuedEnvelope(
                old.sender_id,
                old.conversation_id,
                old.sequence,
                identity,
                old.kind,
                old.payload,
                digest,
                (),
            )
        )
    with sqlite3.connect(spool.root / DATABASE_NAME) as db:
        plans = db.execute(
            "EXPLAIN QUERY PLAN SELECT conversation_id,MIN(sequence) FROM envelopes "
            "WHERE pending=1 GROUP BY conversation_id"
        ).fetchall()
        assert any("pending_conversation_sequence" in row[3] for row in plans)
        assert db.execute("PRAGMA journal_mode").fetchone() == ("delete",)
    spool.close()


def test_multiple_pins_cross_thread_ack_and_no_network_transaction(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"immutable" * 200_000)
    snapshot = snapshot_file(
        root=source, relative_path="data", scope=FileScope.ticket, owner_id="t_one"
    )
    spool = FilesystemSpool(tmp_path / "spool")
    row = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}, snapshots=(snapshot,)
    )
    with ExitStack() as first, ExitStack() as second:
        a = first.enter_context(spool.pin_snapshots(row.submission_id))[0]
        b = second.enter_context(spool.pin_snapshots(row.submission_id))[0]
        with sqlite3.connect(spool.root / DATABASE_NAME, timeout=0.1) as external:
            external.execute("BEGIN EXCLUSIVE")
            external.rollback()
        with ThreadPoolExecutor(max_workers=1) as workers:
            workers.submit(spool.acknowledge, receipt(row)).result()
        assert not spool.has_pending()
        assert a.local_path.read_bytes() == b"immutable" * 200_000
        first.close()
        assert b.local_path.exists()
    assert not b.local_path.exists()
    spool.close()


def test_pin_exception_releases_without_ack_or_deletion(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    row = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    with pytest.raises(RuntimeError, match="cancel"):
        with spool.pin_snapshots(row.submission_id):
            raise RuntimeError("cancel")
    assert spool.pending() == (row,)
    assert (spool.root / "entries" / row.submission_id).exists()
    spool.acknowledge(receipt(row))
    spool.close()


def test_ack_commit_precedes_failed_cleanup_and_restart_never_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    row = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    with monkeypatch.context() as patch:
        patch.setattr(
            shutil, "rmtree", lambda path: (_ for _ in ()).throw(OSError("cleanup failed"))
        )
        with pytest.raises(OSError, match="cleanup failed"):
            spool.acknowledge(receipt(row))
    spool.close()
    spool = FilesystemSpool(tmp_path / "spool")
    assert spool.pending() == ()
    assert not (spool.root / "entries" / row.submission_id).exists()
    assert (
        spool.mint_envelope(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}
        ).sequence
        == 2
    )
    spool.close()


def test_hot_journal_preflight_preserves_bytes_constructor_recovers(tmp_path: Path) -> None:
    root = tmp_path / "executor"
    spool = FilesystemSpool(root / "spool")
    row = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    spool.close()
    code = """import sqlite3, os, sys
c=sqlite3.connect(sys.argv[1]); c.execute('PRAGMA cache_size=1')
c.execute('BEGIN IMMEDIATE'); c.execute('UPDATE envelopes SET envelope_json=randomblob(2000000)')
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(root / "spool" / DATABASE_NAME)], check=True)
    assert (root / "spool" / (DATABASE_NAME + "-journal")).stat().st_size > 0
    before = contents(root)
    with pytest.raises(ValueError, match="recovery required"):
        check_spool_compatibility(root, 2)
    assert contents(root) == before
    spool = FilesystemSpool(root / "spool")
    assert spool.pending() == (row,)
    spool.close()


def test_schema_tamper_and_partial_fresh_initialization_refuse_without_reset(
    tmp_path: Path,
) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    sender = spool.sender_id
    spool.close()
    with sqlite3.connect(tmp_path / "spool" / DATABASE_NAME) as db:
        db.execute("DROP INDEX pending_conversation_sequence")
    before = contents(tmp_path)
    with pytest.raises(ValueError, match="schema structure"):
        FilesystemSpool(tmp_path / "spool")
    assert contents(tmp_path) == before
    assert json.loads((tmp_path / "spool" / "metadata.json").read_bytes())["sender_id"] == sender
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "lock").touch()
    (broken / "entries").mkdir()
    (broken / DATABASE_NAME).touch()
    before = contents(broken)
    with pytest.raises(ValueError):
        FilesystemSpool(broken)
    assert contents(broken) == before


def legacy_spool(root: Path) -> tuple[Path, QueuedEnvelope]:
    spool = root / "spool"
    spool.mkdir()
    (spool / "lock").touch()
    sender = uuid4().hex
    digest = envelope_payload_digest(
        {"value": 1},
        (),
        sender_id=sender,
        conversation_id="c_one",
        sequence=2,
        submission_id="pending",
        kind=EnvelopeKind.cli_write,
    )
    envelope = QueuedEnvelope(
        sender, "c_one", 2, "pending", EnvelopeKind.cli_write, {"value": 1}, digest, ()
    )
    folder = spool / "entries" / "pending"
    folder.mkdir(parents=True)
    (folder / "envelope.json").write_bytes(TypeAdapter(QueuedEnvelope).dump_json(envelope))
    (spool / "metadata.json").write_text(
        json.dumps(
            {
                "format": 1,
                "sender_id": sender,
                "sequences": {"c_one": 4},
                "identities": {
                    "ack": ["c_one", 1, "a" * 64],
                    "pending": ["c_one", 2, digest],
                    "ack-later": ["c_one", 4, "b" * 64],
                },
            }
        )
    )
    (spool / ".incomplete-debug").mkdir()
    (spool / ".incomplete-debug" / "bytes").write_bytes(b"unpublished diagnostic")
    return spool, envelope


def test_offline_conversion_preserves_identity_counters_source_and_lock_inode(
    tmp_path: Path,
) -> None:
    spool, pending = legacy_spool(tmp_path)
    before = contents(spool)
    lock_inode = (spool / "lock").stat().st_ino
    convert_legacy_spool(spool)
    after = contents(spool)
    for name, value in before.items():
        if name != "metadata.json":
            assert after[name] == value
    assert (spool / CONVERSION_DIRECTORY / "metadata-v1.json").read_bytes() == before[
        "metadata.json"
    ]
    assert (spool / "lock").stat().st_ino == lock_inode
    converted = FilesystemSpool(spool)
    assert converted.pending() == (pending,)
    assert (
        converted.mint_envelope(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}
        ).sequence
        == 5
    )
    with sqlite3.connect(spool / DATABASE_NAME) as db:
        assert db.execute(
            "SELECT sequence,payload_sha256 FROM identities WHERE submission_id='ack'"
        ).fetchone() == (1, "a" * 64)
    converted.close()
    before = contents(spool)
    convert_legacy_spool(spool)
    assert contents(spool) == before


@pytest.mark.parametrize("point", ["fence", "backup", "database", "metadata"])
def test_offline_conversion_interruption_retries_without_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
) -> None:
    import planner.executor.outbox_conversion as conversion

    spool, pending = legacy_spool(tmp_path)
    original_metadata = (spool / "metadata.json").read_bytes()
    real_sync = fsync_directory
    real_write = write_file
    real_publish = publish_metadata
    real_replace = os.replace

    def interrupted_sync(path: Path) -> None:
        real_sync(path)
        if point == "fence" and path == spool and (spool / CONVERSION_DIRECTORY).exists():
            raise OSError("crash after fence")

    def interrupted_write(path: Path, data: bytes) -> None:
        if point == "backup" and path.name.startswith(".backup-"):
            path.write_bytes(data[:10])
            raise OSError("crash during backup")
        real_write(path, data)

    def interrupted_replace(source: Path, target: Path) -> None:
        real_replace(source, target)
        if point == "database" and target == spool / DATABASE_NAME:
            raise OSError("crash after database publication")

    def interrupted_publish(path: Path, sender: str) -> None:
        real_publish(path, sender)
        if point == "metadata":
            raise OSError("crash after metadata publication")

    with monkeypatch.context() as patch:
        patch.setattr(conversion, "fsync_directory", interrupted_sync)
        patch.setattr(conversion, "write_file", interrupted_write)
        patch.setattr(os, "replace", interrupted_replace)
        patch.setattr(conversion, "publish_metadata", interrupted_publish)
        with pytest.raises(OSError, match="crash"):
            convert_legacy_spool(spool)
    convert_legacy_spool(spool)
    converted = FilesystemSpool(spool)
    assert converted.pending() == (pending,)
    assert (spool / CONVERSION_DIRECTORY / "metadata-v1.json").read_bytes() == original_metadata
    assert (
        converted.mint_envelope(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}
        ).sequence
        == 5
    )
    converted.close()


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("failed_name", ["envelope.json", "file-0"])
def test_converter_flushes_source_before_publication_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resumed: bool,
    failed_name: str,
) -> None:
    from dataclasses import replace

    import planner.executor.outbox_conversion as conversion
    from planner.executor.files import fsync_file

    spool, pending = legacy_spool(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"retained legacy snapshot")
    snapshot = snapshot_file(
        root=source, relative_path="file", scope=FileScope.ticket, owner_id="t_one"
    )
    manifests = (snapshot.manifest,)
    digest = envelope_payload_digest(
        pending.payload,
        manifests,
        sender_id=pending.sender_id,
        conversation_id=pending.conversation_id,
        sequence=pending.sequence,
        submission_id=pending.submission_id,
        kind=pending.kind,
    )
    pending = replace(pending, file_dependencies=manifests, payload_sha256=digest)
    entry = spool / "entries" / pending.submission_id
    (entry / "envelope.json").write_bytes(TypeAdapter(QueuedEnvelope).dump_json(pending))
    (entry / "file-0").write_bytes(snapshot.local_path.read_bytes())
    metadata = json.loads((spool / "metadata.json").read_bytes())
    metadata["identities"][pending.submission_id][2] = digest
    (spool / "metadata.json").write_text(json.dumps(metadata))

    def interrupted_publish(path: Path, sender: str) -> None:
        raise OSError("interrupted before metadata publication")

    if resumed:
        with monkeypatch.context() as patch:
            patch.setattr(conversion, "publish_metadata", interrupted_publish)
            with pytest.raises(OSError, match="interrupted"):
                convert_legacy_spool(spool)
    before = contents(spool)
    flushed: list[Path] = []
    failed_inode = (entry / failed_name).stat().st_ino

    def failed_flush(descriptor: int) -> None:
        if os.fstat(descriptor).st_ino == failed_inode:
            raise OSError("source flush failed")
        fsync_file(descriptor)

    with monkeypatch.context() as patch:
        patch.setattr(conversion, "fsync_file", failed_flush)
        with pytest.raises(OSError, match="source flush failed"):
            convert_legacy_spool(spool)
    assert contents(spool) == before

    def tracked_flush(descriptor: int) -> None:
        fsync_file(descriptor)
        inode = os.fstat(descriptor).st_ino
        for name in ("file-0", "envelope.json"):
            path = entry / name
            if path.stat().st_ino == inode:
                flushed.append(path)

    def tracked_directory(path: Path) -> None:
        fsync_directory(path)
        flushed.append(path)

    real_replace = os.replace

    def checked_publication(source: Path, target: Path) -> None:
        if target == spool / DATABASE_NAME:
            assert flushed[:4] == [
                entry / "file-0",
                entry / "envelope.json",
                entry,
                spool / "entries",
            ]
        real_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(conversion, "fsync_file", tracked_flush)
        patch.setattr(conversion, "fsync_directory", tracked_directory)
        patch.setattr(os, "replace", checked_publication)
        convert_legacy_spool(spool)
    converted = FilesystemSpool(spool)
    try:
        assert converted.pending() == (pending,)
        assert converted.snapshots(pending.submission_id)[0].local_path.read_bytes() == (
            b"retained legacy snapshot"
        )
    finally:
        converted.close()


def test_converter_refuses_unknown_database_and_active_process_without_mutation(
    tmp_path: Path,
) -> None:
    import fcntl

    spool, _ = legacy_spool(tmp_path)
    with (spool / "lock").open("r+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = contents(spool)
        with pytest.raises(ValueError, match="stop the executor"):
            convert_legacy_spool(spool)
        assert contents(spool) == before
    (spool / DATABASE_NAME).write_bytes(b"unknown database do not overwrite")
    before = contents(spool)
    with pytest.raises(ValueError, match="unknown database"):
        convert_legacy_spool(spool)
    assert contents(spool) == before


def test_database_failure_rolls_back_sequence_and_identity_before_retry(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    spool._db.execute(
        "CREATE TEMP TRIGGER reject_queue BEFORE INSERT ON envelopes "
        "BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
    )
    with pytest.raises(ValueError, match="sequence conflicts"):
        spool.mint_envelope(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}, submission_id="retry"
        )
    assert not spool.has_pending()
    assert spool._db.execute("SELECT 1 FROM identities").fetchone() is None
    assert spool._db.execute("SELECT 1 FROM conversations").fetchone() is None
    spool._db.execute("DROP TRIGGER reject_queue")
    retry = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}, submission_id="retry"
    )
    assert retry.sequence == 1
    spool.close()


def test_failure_after_queue_commit_preserves_exact_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "file").write_bytes(b"must survive")
    snapshot = snapshot_file(
        root=source, relative_path="file", scope=FileScope.ticket, owner_id="t_one"
    )
    spool = FilesystemSpool(tmp_path / "spool")
    real_unlink = Path.unlink

    def failed_cleanup(path: Path, *, missing_ok: bool = False) -> None:
        if path == snapshot.local_path:
            raise OSError("post-commit cleanup failure")
        real_unlink(path, missing_ok=missing_ok)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", failed_cleanup)
        with pytest.raises(OSError, match="post-commit"):
            spool.mint_envelope(
                conversation_id="c_one",
                kind=EnvelopeKind.cli_write,
                payload={},
                snapshots=(snapshot,),
                submission_id="retry",
            )
    committed = spool.pending()[0]
    spool.close()
    spool = FilesystemSpool(tmp_path / "spool")
    retry = spool.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.cli_write,
        payload={},
        snapshots=(snapshot,),
        submission_id="retry",
    )
    assert retry == committed
    assert spool.pending_count() == 1
    assert spool.snapshots("retry")[0].local_path.read_bytes() == b"must survive"
    spool.close()


def test_ack_database_failure_keeps_pending_bytes(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    row = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    spool._db.execute(
        "CREATE TEMP TRIGGER reject_ack BEFORE UPDATE ON envelopes "
        "BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="disk failure"):
        spool.acknowledge(receipt(row))
    assert spool.pending() == (row,)
    assert (spool.root / "entries" / row.submission_id).exists()
    spool.close()


@pytest.mark.parametrize("corrupt", [False, True])
def test_converter_preserves_and_validates_snapshot_bytes(tmp_path: Path, corrupt: bool) -> None:
    from dataclasses import replace

    spool, pending = legacy_spool(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    original = b"\x00\xff" * 200_000
    (source / "file").write_bytes(original)
    snapshot = snapshot_file(
        root=source, relative_path="file", scope=FileScope.ticket, owner_id="t_one"
    )
    manifests = (snapshot.manifest,)
    digest = envelope_payload_digest(
        pending.payload,
        manifests,
        sender_id=pending.sender_id,
        conversation_id=pending.conversation_id,
        sequence=pending.sequence,
        submission_id=pending.submission_id,
        kind=pending.kind,
    )
    pending = replace(pending, file_dependencies=manifests, payload_sha256=digest)
    entry = spool / "entries" / pending.submission_id
    (entry / "envelope.json").write_bytes(TypeAdapter(QueuedEnvelope).dump_json(pending))
    (entry / "file-0").write_bytes(b"bad" if corrupt else original)
    metadata = json.loads((spool / "metadata.json").read_bytes())
    metadata["identities"][pending.submission_id][2] = digest
    (spool / "metadata.json").write_text(json.dumps(metadata))
    before = contents(spool)
    if corrupt:
        with pytest.raises(ValueError, match="corrupt immutable snapshot"):
            convert_legacy_spool(spool)
        assert contents(spool) == before
    else:
        convert_legacy_spool(spool)
        converted = FilesystemSpool(spool)
        assert converted.pending() == (pending,)
        assert converted.snapshots(pending.submission_id)[0].local_path.read_bytes() == original
        converted.close()


@pytest.mark.parametrize("with_fence", [False, True])
def test_converter_refuses_dangling_database_symlink(tmp_path: Path, with_fence: bool) -> None:
    spool, _ = legacy_spool(tmp_path)
    if with_fence:
        fence = spool / CONVERSION_DIRECTORY
        fence.mkdir()
        (fence / "metadata-v1.json").write_bytes((spool / "metadata.json").read_bytes())
    target = tmp_path / "absent"
    (spool / DATABASE_NAME).symlink_to(target)
    original_metadata = (spool / "metadata.json").read_bytes()
    with pytest.raises(ValueError, match="unknown database"):
        convert_legacy_spool(spool)
    assert (spool / DATABASE_NAME).is_symlink()
    assert not target.exists()
    assert (spool / "metadata.json").read_bytes() == original_metadata


def test_converter_refuses_foreign_sender_database_after_fence(tmp_path: Path) -> None:
    from planner.executor.outbox_storage import connect_database, initialize_database

    spool, _ = legacy_spool(tmp_path)
    fence = spool / CONVERSION_DIRECTORY
    fence.mkdir()
    (fence / "metadata-v1.json").write_bytes((spool / "metadata.json").read_bytes())
    db = connect_database(spool / DATABASE_NAME)
    initialize_database(db, uuid4().hex)
    db.close()
    before = contents(spool)
    with pytest.raises(ValueError, match="sender differs"):
        convert_legacy_spool(spool)
    assert contents(spool) == before


def test_fresh_directory_ancestors_are_synced_before_child_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import planner.executor.outbox_storage as storage

    root = tmp_path / "new" / "executor" / "spool"
    calls: list[Path] = []
    real_sync = fsync_directory

    def synced(path: Path) -> None:
        calls.append(path)
        if path == tmp_path and (tmp_path / "new").exists():
            assert not (tmp_path / "new" / "executor").exists()
        if path == tmp_path / "new":
            assert not root.exists()
        real_sync(path)

    monkeypatch.setattr(storage, "fsync_directory", synced)
    spool = FilesystemSpool(root)
    assert calls.index(tmp_path) < calls.index(tmp_path / "new") < calls.index(root.parent)
    spool.close()


@pytest.mark.parametrize("after_ack", [False, True])
def test_exact_retry_accepts_reordered_nested_payload_preserves_original_bytes(
    tmp_path: Path,
    after_ack: bool,
) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    original_payload: JsonObject = {"a": 1, "b": {"x": 2, "y": 3}}
    row = spool.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.cli_write,
        payload=original_payload,
        submission_id="stable",
    )
    original_encoded = (spool.root / "entries" / "stable" / "envelope.json").read_bytes()
    if after_ack:
        spool.acknowledge(receipt(row))
    retry = spool.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.cli_write,
        payload={"b": {"y": 3, "x": 2}, "a": 1},
        submission_id="stable",
    )
    assert retry == row
    assert (spool.root / "entries" / "stable" / "envelope.json").read_bytes() == original_encoded
    assert spool._db.execute(
        "SELECT envelope_json FROM envelopes WHERE submission_id='stable'"
    ).fetchone() == (original_encoded,)
    spool.close()


def test_busy_writer_expires_without_consuming_identity_or_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import planner.executor.outbox_storage as storage

    monkeypatch.setattr(storage, "OUTBOX_BUSY_TIMEOUT_MS", 25)
    spool = FilesystemSpool(tmp_path / "spool")
    with sqlite3.connect(spool.root / DATABASE_NAME) as competitor:
        competitor.execute("BEGIN IMMEDIATE")
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            spool.mint_envelope(
                conversation_id="c_one",
                kind=EnvelopeKind.cli_write,
                payload={},
                submission_id="retry",
            )
        assert 0.01 < time.monotonic() - started < 1
        assert spool.pending_count() == 0
        assert spool._db.execute("SELECT 1 FROM identities").fetchone() is None
        competitor.rollback()
    retry = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}, submission_id="retry"
    )
    assert retry.sequence == 1
    spool.close()


def test_second_process_owner_refuses_without_replacing_lock_or_state(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    row = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    before = contents(spool.root)
    lock_inode = (spool.root / "lock").stat().st_ino
    with pytest.raises(BlockingIOError):
        FilesystemSpool(spool.root)
    assert contents(spool.root) == before
    assert (spool.root / "lock").stat().st_ino == lock_inode
    assert spool.pending() == (row,)
    spool.close()


def test_offline_conversion_cli_reports_result_and_sqlite_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from click.testing import CliRunner

    import planner.executor.outbox_conversion as conversion
    from planner.executor.cli import executor_group

    legacy_spool(tmp_path)
    result = CliRunner().invoke(executor_group, ["convert-spool", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "Spool format 2 is ready" in result.output

    def failed(path: Path) -> None:
        raise sqlite3.DatabaseError("invalid legacy database")

    monkeypatch.setattr(conversion, "convert_legacy_spool", failed)
    result = CliRunner().invoke(executor_group, ["convert-spool", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Error: invalid legacy database" in result.output
