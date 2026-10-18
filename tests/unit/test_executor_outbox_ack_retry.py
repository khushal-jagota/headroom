"""Exact retries recover from partial acknowledgment cleanup without a restart."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileScope,
    FileSnapshot,
    ReceiptDisposition,
)
from planner.executor.files import snapshot_file
from planner.executor.journal import FilesystemSpool
from planner.executor.outbox_storage import DATABASE_NAME


def test_exact_retry_resumes_partial_ack_cleanup_in_same_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    content = b"immutable retry bytes"
    (source / "data").write_bytes(content)
    snapshot = snapshot_file(
        root=source, relative_path="data", scope=FileScope.ticket, owner_id="t_one"
    )
    spool = FilesystemSpool(tmp_path / "spool")
    try:
        envelope = spool.mint_envelope(
            conversation_id="c_one",
            kind=EnvelopeKind.cli_write,
            payload={"value": 1},
            snapshots=(snapshot,),
        )
        folder = spool.root / "entries" / envelope.submission_id

        def partial_cleanup(path: Path) -> None:
            assert path == folder
            (path / "envelope.json").unlink()
            raise OSError("partial cleanup failed")

        with monkeypatch.context() as patch:
            patch.setattr(shutil, "rmtree", partial_cleanup)
            with pytest.raises(OSError, match="partial cleanup failed"):
                spool.acknowledge(
                    AcceptanceReceipt(
                        envelope.sender_id,
                        envelope.submission_id,
                        envelope.payload_sha256,
                        ReceiptDisposition.accepted,
                        {},
                        1,
                    )
                )
        assert folder.exists()
        assert not (folder / "envelope.json").exists()
        assert spool.pending() == ()
        with sqlite3.connect(spool.root / DATABASE_NAME) as database:
            assert database.execute(
                "SELECT pending FROM envelopes WHERE submission_id=?",
                (envelope.submission_id,),
            ).fetchone() == (0,)

        retry_snapshot = FileSnapshot(snapshot.manifest, source / "data")
        retry = spool.mint_envelope(
            conversation_id=envelope.conversation_id,
            kind=envelope.kind,
            payload=envelope.payload,
            snapshots=(retry_snapshot,),
            submission_id=envelope.submission_id,
        )
        assert retry == envelope
        assert spool.pending_heads() == (envelope,)
        with spool.pin_snapshots(envelope.submission_id) as snapshots:
            assert snapshots[0].local_path.read_bytes() == content
        assert spool.mint_envelope(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}
        ).sequence == envelope.sequence + 1
    finally:
        spool.close()
