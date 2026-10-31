"""Durability and ingress checks for the executor's real filesystem transport."""

from __future__ import annotations

import asyncio
import struct
from pathlib import Path

import pytest

from planner.execution_identity import ProtocolRefused
from planner.executor.mac_outbox import MacOutbox
from planner.executor.protocol import (
    AcceptanceReceipt,
    EnvelopeKind,
    IpcResponse,
    OutboxEntry,
    ReceiptDisposition,
    encode_frame,
    read_typed_frame,
)


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
