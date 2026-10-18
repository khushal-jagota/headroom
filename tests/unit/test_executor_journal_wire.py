"""Durability and ingress checks for the executor's real filesystem transport."""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path

import pytest

from planner.executor.configuration import PROTOCOL_VERSION
from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    FileScope,
    JsonObject,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
)
from planner.executor.files import snapshot_file
from planner.executor.journal import FilesystemSpool
from planner.executor.wire import ProtocolRefused, encode_frame, read_frame


def test_journal_restarts_after_ack_without_reusing_sequence(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    first = spool.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.cli_write,
        payload={"value": 1},
        submission_id="stable",
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
    reopened = FilesystemSpool(tmp_path / "spool")
    second = reopened.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={"value": 2}
    )
    assert second.sequence == 2
    assert second.sender_id == first.sender_id
    retry = reopened.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.cli_write,
        payload={"value": 1},
        submission_id="stable",
    )
    assert retry == first
    with pytest.raises(ValueError, match="different content"):
        reopened.mint_envelope(
            conversation_id="c_one",
            kind=EnvelopeKind.cli_write,
            payload={"value": 3},
            submission_id="stable",
        )
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
    spool = FilesystemSpool(tmp_path / "spool")
    envelope = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={}, snapshots=(snapshot,)
    )
    (source / "binary").write_bytes(b"changed")
    durable = spool.snapshots(envelope.submission_id)[0]
    assert durable.local_path.read_bytes() == original
    assert spool.pending() == (envelope,)
    spool.close()


def test_incomplete_entry_not_replayed_and_bad_receipt_keeps_entry(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    incomplete = spool.root / ".incomplete-test"
    incomplete.mkdir()
    (incomplete / "envelope.json").write_text("partial")
    envelope = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    with pytest.raises(ValueError, match="digest"):
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
    reopened = FilesystemSpool(tmp_path / "spool")
    assert reopened.pending() == (envelope,)
    reopened.close()


def test_frame_roundtrip_and_oversize_before_body_read() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        message = WireMessage(WireMessageKind.health, "request", {})
        reader.feed_data(encode_frame(message))
        assert await read_frame(reader) == message
        bad = asyncio.StreamReader()
        bad.feed_data(struct.pack("!I", 1024 * 1024 + 1))
        with pytest.raises(ProtocolRefused):
            await read_frame(bad)

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

    from planner.executor.wire import parse_identity

    valid: dict[str, object] = {
        "protocol_version": PROTOCOL_VERSION,
        "spool_format_version": 1,
        "application_revision": "a" * 40,
        "executor_incarnation": str(uuid4()),
        "connection_generation": 1,
    }
    assert parse_identity(valid).connection_generation == 1
    for malformed in (
        {**valid, "protocol_version": True},
        {**valid, "connection_generation": "1"},
        {**valid, "application_revision": "dev"},
        {**valid, "extra": "ignored"},
    ):
        with pytest.raises(ProtocolRefused):
            parse_identity(malformed)


def test_input_path_not_visible_before_verified_complete(tmp_path: Path) -> None:
    from planner.executor.files import IncomingFiles

    receiver = IncomingFiles(tmp_path / "managed", tmp_path / "quarantine")
    original = tmp_path / "source"
    original.mkdir()
    (original / "image").write_bytes(b"\x00binary")
    snapshot = snapshot_file(
        root=original, relative_path="image", scope=FileScope.conversation, owner_id="c_one"
    )
    receiver.begin(snapshot.manifest)
    target = tmp_path / "managed" / "conversations" / "c_one" / "image"
    assert not target.exists()
    with pytest.raises(ValueError, match="incomplete"):
        receiver.complete(snapshot.manifest)
    receiver.receive_path(snapshot.manifest, snapshot.local_path)
    assert target.read_bytes() == b"\x00binary"


def test_disk_failure_never_grants_queue_receipt_or_consumes_sequence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    spool = FilesystemSpool(tmp_path / "spool")
    real_fsync = os.fsync

    def disk_full(descriptor: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", disk_full)
    with pytest.raises(OSError, match="disk full"):
        spool.mint_envelope(
            conversation_id="c_one",
            kind=EnvelopeKind.cli_write,
            payload={"value": 1},
            submission_id="retry",
        )
    assert spool.pending() == ()
    monkeypatch.setattr(os, "fsync", real_fsync)
    retried = spool.mint_envelope(
        conversation_id="c_one",
        kind=EnvelopeKind.cli_write,
        payload={"value": 1},
        submission_id="retry",
    )
    assert retried.sequence == 1
    spool.close()


def test_oversized_envelope_never_becomes_a_queued_poison_entry(tmp_path: Path) -> None:
    spool = FilesystemSpool(tmp_path / "spool")
    with pytest.raises(ProtocolRefused, match="limit"):
        spool.mint_envelope(
            conversation_id="c_one",
            kind=EnvelopeKind.cli_write,
            payload={"text": "x" * (1024 * 1024)},
        )
    assert spool.pending() == ()
    first = spool.mint_envelope(conversation_id="c_one", kind=EnvelopeKind.cli_write, payload={})
    assert first.sequence == 1
    spool.close()


def test_input_full_retry_discards_partial_quarantine_without_publishing_it(tmp_path: Path) -> None:
    from planner.executor.files import IncomingFiles

    source = tmp_path / "source"
    source.mkdir()
    contents = b"binary\x00\xff" * 60_000
    (source / "file").write_bytes(contents)
    snapshot = snapshot_file(
        root=source, relative_path="file", scope=FileScope.conversation, owner_id="c_one"
    )
    receiver = IncomingFiles(tmp_path / "managed", tmp_path / "quarantine")
    incomplete = tmp_path / "incomplete"
    incomplete.write_bytes(contents[:100])
    with pytest.raises(ValueError, match="incomplete"):
        receiver.receive_path(snapshot.manifest, incomplete)
    assert not (tmp_path / "managed" / "conversations" / "c_one" / "file").exists()
    # The link drops. The same publication restarts at offset zero on reconnect.
    target = receiver.receive_path(snapshot.manifest, snapshot.local_path)
    assert target.read_bytes() == contents


def test_version_one_permission_spool_payload_reads_unchanged_under_new_protocol(
    tmp_path: Path,
) -> None:
    from uuid import uuid4

    from pydantic import TypeAdapter

    from planner.executor.contracts import AnswerApplication, AskKind

    spool = FilesystemSpool(tmp_path / "spool")
    # Exact old payload shape: no AskReference.kind field, spool format stays 1.
    payload: JsonObject = {
        "answer_id": "legacy-answer",
        "applied_at_mac_ms": 10,
        "ask": {
            "child": {
                "executor_incarnation": str(uuid4()),
                "child_id": str(uuid4()),
                "conversation_id": "c_one",
            },
            "turn": {"conversation_id": "c_one", "turn_number": 1},
            "ask_id": "ask",
        },
    }
    row = spool.mint_envelope(
        conversation_id="c_one", kind=EnvelopeKind.answer_application, payload=payload
    )
    entry = tmp_path / "spool" / "entries" / row.submission_id / "envelope.json"
    before = entry.read_bytes()
    spool.close()
    reopened = FilesystemSpool(tmp_path / "spool")
    assert reopened.pending() == (row,)
    parsed = TypeAdapter(AnswerApplication).validate_python(reopened.pending()[0].payload)
    assert parsed.ask.kind == AskKind.permission
    assert entry.read_bytes() == before
    reopened.close()
