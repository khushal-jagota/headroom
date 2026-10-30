"""SQLite atomicity, pinned transfer bytes, and indexed heads."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileScope,
    FileSnapshot,
    JsonObject,
    QueuedEnvelope,
    ReceiptDisposition,
)
from planner.executor.files import fsync_directory, snapshot_file
from planner.executor.journal import FilesystemSpool, envelope_payload_digest
from planner.executor.outbox_storage import DATABASE_NAME
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


def test_indexed_heads_counts_and_permanent_sequence_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    rows = [
        spool.mint_envelope(conversation_id=c, kind=EnvelopeKind.cli_write, payload={"n": n})
        for c, n in (("c_b", 1), ("c_a", 1), ("c_b", 2), ("c_a", 2))
    ]
    assert spool.pending_heads() == (rows[1], rows[0])
    with monkeypatch.context() as metadata_only:

        def forbid_decode(*args: object, **kwargs: object) -> None:
            raise AssertionError("sequence snapshot decoded an envelope")

        metadata_only.setattr("planner.executor.journal.TypeAdapter", forbid_decode)
        assert spool.pending_sequence_ceilings() == {"c_a": 2, "c_b": 2}
    assert spool.pending_count() == 4
    assert spool.has_pending() and spool.has_pending("c_a")
    assert not spool.has_pending("c_absent")
    spool.acknowledge(receipt(rows[1]))
    assert spool.pending_heads() == (rows[3], rows[0])
    assert spool.pending_count() == 3
    spool.acknowledge(receipt(rows[3]))
    assert spool.pending_sequence_ceilings() == {"c_b": 2}
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


def test_equal_event_bytes_share_inodes_across_restart_and_pinned_cleanup(tmp_path: Path) -> None:
    """Each event owns identity and lifetime independently of its shared bytes."""
    source = tmp_path / "source"
    source.mkdir()
    artifact = source / "data"
    original = b"x" * (1024 * 1024)
    artifact.write_bytes(original)
    spool = FilesystemSpool(tmp_path / "spool")

    def publish() -> QueuedEnvelope:
        snapshot = snapshot_file(
            root=source, relative_path="data", scope=FileScope.ticket, owner_id="t_one"
        )
        return spool.mint_envelope(
            conversation_id="c_one",
            kind=EnvelopeKind.backend_event,
            payload={},
            snapshots=(snapshot,),
        )

    rows = [publish() for _ in range(3)]
    paths = [spool.snapshots(row.submission_id)[0].local_path for row in rows]
    assert len({p.stat().st_ino for p in paths}) == 1
    assert len({row.file_dependencies[0].publication_id for row in rows}) == 3
    assert len({row.submission_id for row in rows}) == 3
    artifact.write_bytes(b"changed")
    changed = publish()
    assert (
        spool.snapshots(changed.submission_id)[0].local_path.stat().st_ino != paths[0].stat().st_ino
    )
    spool.close()

    before = contents(tmp_path / "spool")
    check_spool_compatibility(tmp_path / "spool", 2)
    assert contents(tmp_path / "spool") == before
    spool = FilesystemSpool(tmp_path / "spool")
    artifact.write_bytes(original)
    resumed = publish()
    assert (
        spool.snapshots(resumed.submission_id)[0].local_path.stat().st_ino == paths[0].stat().st_ino
    )
    with spool.pin_snapshots(rows[0].submission_id) as pinned:
        with ThreadPoolExecutor(max_workers=1) as workers:
            workers.submit(spool.acknowledge, receipt(rows[0])).result()
        spool.acknowledge(receipt(rows[1]))
        assert pinned[0].local_path.read_bytes() == original
        assert paths[2].read_bytes() == original
    assert not paths[0].exists()
    assert paths[2].read_bytes() == original
    for row in (rows[2], resumed, changed):
        spool.acknowledge(receipt(row))
    assert not list((spool.root / "entries").iterdir())
    spool.close()


@pytest.mark.parametrize("boundary", ["incoming", "retained", "unsupported_link"])
def test_shared_snapshot_verification_and_filesystem_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    import errno

    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"original")
    spool = FilesystemSpool(tmp_path / "spool")

    def capture() -> FileSnapshot:
        return snapshot_file(
            root=source, relative_path="data", scope=FileScope.ticket, owner_id="t_one"
        )

    first = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.backend_event, payload={}, snapshots=(capture(),)
    )
    retained = spool.snapshots(first.submission_id)[0].local_path
    snapshot = capture()
    if boundary == "incoming":
        snapshot.local_path.write_bytes(b"corrupt")
    elif boundary == "retained":
        retained.write_bytes(b"corrupt")
    else:

        def unsupported(*args: object, **kwargs: object) -> None:
            raise OSError(errno.EOPNOTSUPP, "hard links unsupported")

        monkeypatch.setattr("planner.executor.journal.os.link", unsupported)
    if boundary == "unsupported_link":
        second = spool.mint_envelope(
            conversation_id="c_one",
            kind=EnvelopeKind.backend_event,
            payload={},
            snapshots=(snapshot,),
        )
        copied = spool.snapshots(second.submission_id)[0].local_path
        assert copied.read_bytes() == b"original"
        assert copied.stat().st_ino != retained.stat().st_ino
    else:
        with pytest.raises(ValueError, match="corrupt immutable"):
            spool.mint_envelope(
                conversation_id="c_one",
                kind=EnvelopeKind.backend_event,
                payload={},
                snapshots=(snapshot,),
            )
        assert spool.pending() == (first,)
    spool.close()


def test_interrupted_link_publication_preserves_original_and_exact_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data").write_bytes(b"retained")
    spool = FilesystemSpool(tmp_path / "spool")

    def capture() -> FileSnapshot:
        return snapshot_file(
            root=source, relative_path="data", scope=FileScope.ticket, owner_id="t_one"
        )

    first = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.backend_event, payload={}, snapshots=(capture(),)
    )
    snapshot = capture()
    original_fsync = fsync_directory

    def fail_after_rename(path: Path) -> None:
        original_fsync(path)
        if path == spool.root / "entries":
            raise OSError("interrupted directory flush")

    with monkeypatch.context() as failure:
        failure.setattr("planner.executor.journal.fsync_directory", fail_after_rename)
        with pytest.raises(OSError, match="interrupted"):
            spool.mint_envelope(
                conversation_id="c_one",
                kind=EnvelopeKind.backend_event,
                payload={},
                snapshots=(snapshot,),
                submission_id="interrupted",
            )
    assert spool.pending() == (first,)
    original = spool.snapshots(first.submission_id)[0].local_path
    assert original.read_bytes() == b"retained"
    retried = spool.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.backend_event,
        payload={},
        snapshots=(snapshot,),
        submission_id="interrupted",
    )
    assert (
        spool.snapshots(retried.submission_id)[0].local_path.stat().st_ino == original.stat().st_ino
    )
    spool.close()


def test_ticket_capture_reuses_pending_and_acknowledged_bytes_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import files
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.contracts import ChildReference

    paths = ExecutorPaths(tmp_path)
    directory = paths.artifacts / "t_one"
    directory.mkdir(parents=True)
    (directory / "image").write_bytes(b"A")
    child = ChildReference("incarnation", "child", "c_one")
    spool = FilesystemSpool(paths.spool)
    original = files.snapshot_file
    captures = []

    def capture(**kwargs: object) -> FileSnapshot:
        value = original(**kwargs)  # type: ignore[arg-type]
        captures.append(value.manifest)
        return value

    monkeypatch.setattr(files, "snapshot_file", capture)

    def mint() -> QueuedEnvelope:
        return spool.mint_ticket_envelope(
            paths=paths, ticket_id="t_one", child=child, kind=EnvelopeKind.cli_write,
            payload={"child": TypeAdapter(ChildReference).dump_python(child, mode="json")},
        )

    try:
        first = mint()
        second = mint()
        assert len(captures) == 1
        assert second.file_dependencies == first.file_dependencies
        second_path = spool.snapshots(second.submission_id)[0].local_path
        first_path = spool.snapshots(first.submission_id)[0].local_path
        assert second_path.stat().st_ino == first_path.stat().st_ino
        assert not spool.dependency_acknowledged(second, second.file_dependencies[0])
        spool.acknowledge(receipt(first))
        assert second_path.read_bytes() == b"A"
        spool.close()
        spool = FilesystemSpool(paths.spool)
        assert spool.dependency_acknowledged(second, second.file_dependencies[0])
        spool.acknowledge(receipt(second))
        assert mint().file_dependencies == ()
        assert len(captures) == 1
        (directory / "image").write_bytes(b"B")
        changed = mint()
        (directory / "image").write_bytes(b"A")
        returned = mint()
        assert len(captures) == 3
        assert returned.file_dependencies != first.file_dependencies
        assert spool.snapshots(changed.submission_id)[0].local_path.read_bytes() == b"B"
        assert spool.snapshots(returned.submission_id)[0].local_path.read_bytes() == b"A"
        child = ChildReference("incarnation", "replacement", "c_two")
        assert mint().file_dependencies != returned.file_dependencies
    finally:
        spool.close()


def test_failed_ticket_capture_does_not_advance_comparison_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import files
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.contracts import ChildReference

    paths = ExecutorPaths(tmp_path)
    directory = paths.artifacts / "t_one"
    directory.mkdir(parents=True)
    source = directory / "image"
    source.write_bytes(b"A")
    child = ChildReference("incarnation", "child", "c_one")
    spool = FilesystemSpool(paths.spool)
    original = files.snapshot_file

    def changed_capture(**kwargs: object) -> FileSnapshot:
        source.write_bytes(b"B")
        return original(**kwargs)  # type: ignore[arg-type]

    try:
        with monkeypatch.context() as patch:
            patch.setattr(files, "snapshot_file", changed_capture)
            with pytest.raises(ValueError, match="changed during"):
                spool.mint_ticket_envelope(
                    paths=paths, ticket_id="t_one", child=child,
                    kind=EnvelopeKind.cli_write,
                    payload={"child": TypeAdapter(ChildReference).dump_python(child, mode="json")},
                )
        assert spool.pending_count() == 0
        assert list((directory.parent / ".executor-snapshots").iterdir()) == []
        queued = spool.mint_ticket_envelope(
            paths=paths, ticket_id="t_one", child=child,
            kind=EnvelopeKind.cli_write,
                    payload={"child": TypeAdapter(ChildReference).dump_python(child, mode="json")},
        )
        assert queued.sequence == 1
        assert spool.snapshots(queued.submission_id)[0].local_path.read_bytes() == b"B"
    finally:
        spool.close()


def test_format_one_remains_untouched_and_unsupported(tmp_path: Path) -> None:
    root = tmp_path / "spool"
    root.mkdir()
    (root / "metadata.json").write_text('{"format":1,"sender_id":"old"}')
    before = contents(tmp_path)
    with pytest.raises(ValueError, match="incompatible spool"):
        check_spool_compatibility(tmp_path, 2)
    assert contents(tmp_path) == before


def test_capture_recompares_if_an_envelope_commits_during_file_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.executor import journal
    from planner.executor.configuration import ExecutorPaths
    from planner.executor.contracts import ChildReference

    paths = ExecutorPaths(tmp_path)
    directory = paths.artifacts / "t_one"
    directory.mkdir(parents=True)
    source = directory / "image"
    source.write_bytes(b"A")
    child = ChildReference("incarnation", "child", "c_one")
    payload = {"child": TypeAdapter(ChildReference).dump_python(child, mode="json")}
    spool = FilesystemSpool(paths.spool)
    original = journal.snapshot_ticket
    interleaved = False

    def capture(*args: object, **kwargs: object) -> tuple[FileSnapshot, ...]:
        nonlocal interleaved
        captured = original(*args, **kwargs)  # type: ignore[arg-type]
        if not interleaved:
            interleaved = True
            source.write_bytes(b"B")
            spool.mint_envelope(
                conversation_id=child.conversation_id, kind=EnvelopeKind.cli_write,
                payload=payload, snapshots=original(paths, "t_one"),
            )
        return captured

    monkeypatch.setattr(journal, "snapshot_ticket", capture)
    try:
        result = spool.mint_ticket_envelope(
            paths=paths, ticket_id="t_one", child=child,
            kind=EnvelopeKind.cli_write, payload=payload,
        )
        assert result.sequence == 2
        assert spool.snapshots(result.submission_id)[0].local_path.read_bytes() == b"B"
        assert spool.pending()[0].file_dependencies == result.file_dependencies
        assert list((directory.parent / ".executor-snapshots").iterdir()) == []
    finally:
        spool.close()
