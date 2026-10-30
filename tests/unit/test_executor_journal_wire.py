"""Durability and ingress checks for the executor's real filesystem transport."""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path

import pytest

from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ProtocolRefused
from planner.executor.files import snapshot_file
from planner.executor.mac_outbox import MacOutbox
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileScope,
    IpcResponse,
    OutboxEntry,
    ReceiptDisposition,
    encode_frame,
    read_typed_frame,
)


def test_journal_restarts_after_ack_without_reusing_sequence(tmp_path: Path) -> None:
    spool = MacOutbox(tmp_path / "spool")
    first = spool.add(
        OutboxEntry(
            conversation_id="c_one",
            kind=EnvelopeKind.cli_write,
            payload={"value": 1},
            submission_id="stable",
        )
    )
    spool.acknowledge(
        AcceptanceReceipt(
            first.sender_id,
            first.submission_id,
            first.payload_sha256,
            ReceiptDisposition.accepted,
            {"original": True},
            1,
        )
    )
    spool.close()
    reopened = MacOutbox(tmp_path / "spool")
    second = reopened.add(
        OutboxEntry(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={"value": 2})
    )
    assert second.sequence == 2
    assert second.sender_id == first.sender_id
    assert reopened.lookup("stable") is None
    reopened.close()


def test_snapshot_bytes_immutable_after_enqueue(tmp_path: Path) -> None:
    source = tmp_path / "artifacts"
    source.mkdir()
    original = b"\x00\xff" * 200_000
    (source / "binary").write_bytes(original)
    snapshot = snapshot_file(
        root=source, relative_path="binary", scope=FileScope.ticket, owner_id="t_example"
    )
    (source / "binary").write_bytes(b"changed before enqueue")
    spool = MacOutbox(tmp_path / "spool")
    envelope = spool.add(
        OutboxEntry(
            conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}, snapshots=(snapshot,)
        )
    )
    (source / "binary").write_bytes(b"changed")
    durable = spool.snapshots(envelope.submission_id)[0]
    assert durable.local_path.read_bytes() == original
    assert spool.pending() == (envelope,)
    spool.close()


def test_incomplete_entry_not_replayed_and_bad_receipt_keeps_entry(tmp_path: Path) -> None:
    spool = MacOutbox(tmp_path / "spool")
    envelope = spool.add(
        OutboxEntry(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    )
    with pytest.raises(ValueError, match="identity"):
        spool.acknowledge(
            AcceptanceReceipt(
                envelope.sender_id,
                envelope.submission_id,
                "bad",
                ReceiptDisposition.accepted,
                {},
                1,
            )
        )
    spool.close()
    reopened = MacOutbox(tmp_path / "spool")
    assert reopened.pending() == (envelope,)
    reopened.close()


def test_frame_roundtrip_and_oversize_before_body_read() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        message = IpcResponse("request", {}, None)
        reader.feed_data(encode_frame(message))
        assert await read_typed_frame(reader, IpcResponse) == message
        bad = asyncio.StreamReader()
        bad.feed_data(struct.pack("!I", 1024 * 1024 + 1))
        with pytest.raises(ProtocolRefused):
            await read_typed_frame(bad, IpcResponse)

    asyncio.run(run())


def test_symlink_snapshot_refused(tmp_path: Path) -> None:
    (tmp_path / "outside").write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(tmp_path / "outside")
    with pytest.raises(ValueError, match="symlink"):
        snapshot_file(root=root, relative_path="link", scope=FileScope.ticket, owner_id="t_one")


def test_strict_identity_refuses_coercion_unknown_fields_and_short_sha() -> None:
    from uuid import uuid4

    from planner.execution_identity import parse_identity

    valid: dict[str, object] = {
        "protocol_version": PROTOCOL_VERSION,
        "spool_format_version": SPOOL_FORMAT_VERSION,
        "application_revision": "dev",
        "executor_incarnation": str(uuid4()),
        "connection_generation": 1,
    }
    assert parse_identity(valid).connection_generation == 1
    for malformed in (
        {**valid, "protocol_version": True},
        {**valid, "protocol_version": 13},
        {**valid, "connection_generation": "1"},
        {**valid, "application_revision": " "},
        {**valid, "extra": "ignored"},
    ):
        with pytest.raises(ProtocolRefused):
            parse_identity(malformed)


def test_disk_failure_never_grants_queue_receipt_or_consumes_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    spool = MacOutbox(tmp_path / "spool")
    real_fsync = os.fsync

    def disk_full(descriptor: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(OSError, match="disk full"):
        spool.add(
            OutboxEntry(
                conversation_id="c_one",
                kind=EnvelopeKind.cli_write,
                payload={"value": 1},
                submission_id="retry",
            )
        )
    assert spool.pending() == ()
    monkeypatch.setattr(os, "fsync", real_fsync)
    retried = spool.add(
        OutboxEntry(
            conversation_id="c_one",
            kind=EnvelopeKind.cli_write,
            payload={"value": 1},
            submission_id="retry",
        )
    )
    assert retried.sequence == 1
    spool.close()


def test_oversized_envelope_never_becomes_a_queued_poison_entry(tmp_path: Path) -> None:
    spool = MacOutbox(tmp_path / "spool")
    with pytest.raises(ProtocolRefused, match="limit"):
        spool.add(
            OutboxEntry(
                conversation_id="c_one",
                kind=EnvelopeKind.cli_write,
                payload={"text": "x" * (1024 * 1024)},
            )
        )
    assert spool.pending() == ()
    first = spool.add(OutboxEntry(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}))
    assert first.sequence == 1
    spool.close()
