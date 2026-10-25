"""Transfer lifetime and Mac durability boundaries around the SQLite outbox."""

from __future__ import annotations

import asyncio
import fcntl
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from planner.executor import files
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    EnvelopeKind,
    ExecutorSshConfig,
    FileScope,
    JsonObject,
    ReceiptDisposition,
    SshAuthenticationMode,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import MacExecutor, as_object


@pytest.mark.parametrize("cancel", [False, True])
def test_replay_pin_retains_bytes_after_concurrent_receipt_until_transfer_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    async def run() -> None:
        mac = MacExecutor(
            paths=ExecutorPaths(tmp_path / "mac"),
            revision="a" * 40,
            ssh_config=ExecutorSshConfig(
                "unused",
                1,
                "unused",
                tmp_path / "key",
                tmp_path / "hosts",
                SshAuthenticationMode.public_key,
            ),
            panels_server_url="http://unused",
        )
        # Isolate outbox lifetime after the peer has accepted this generation.
        mac._handshake_accepted = True
        source = tmp_path / "source"
        source.mkdir()
        (source / "artifact").write_bytes(b"immutable bytes")
        snapshot = files.snapshot_file(
            root=source, relative_path="artifact", scope=FileScope.ticket, owner_id="t_one"
        )
        row = mac.spool.mint_envelope(
            conversation_id="c_one",
            kind=EnvelopeKind.cli_write,
            payload={"child": {}},
            snapshots=(snapshot,),
        )
        durable_path = mac.spool.snapshots(row.submission_id)[0].local_path
        receipt = AcceptanceReceipt(
            row.sender_id,
            row.submission_id,
            row.payload_sha256,
            ReceiptDisposition.accepted,
            {},
            100,
        )
        entered = asyncio.Event()
        release = asyncio.Event()

        async def request(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            if kind is WireMessageKind.file_manifest:
                entered.set()
                await release.wait()
                assert durable_path.read_bytes() == b"immutable bytes"
                return WireMessage(
                    WireMessageKind.file_accepted, "file", as_object(snapshot.manifest)
                )
            assert kind is WireMessageKind.envelope
            return WireMessage(WireMessageKind.receipt, "row", as_object(receipt))

        monkeypatch.setattr(mac, "_request", request)
        task = asyncio.create_task(mac._replay_envelope(row))
        try:
            await entered.wait()
            # A separate writer obtains exclusive access while network I/O waits:
            # the transfer pin must not retain even a read transaction.
            with sqlite3.connect(mac.paths.spool / "outbox.sqlite3", timeout=0) as other:
                other.execute("BEGIN EXCLUSIVE")
                other.rollback()
            # Reconciliation can deliver a first receipt while transfer awaits I/O.
            await asyncio.to_thread(mac.spool.acknowledge, receipt)
            assert not mac.spool.has_pending()
            assert durable_path.read_bytes() == b"immutable bytes"
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                await task
            assert not durable_path.exists()
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            mac.spool.close()

    asyncio.run(run())


def test_mac_full_flush_failure_propagates_after_regular_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, int]] = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(fcntl, "F_FULLFSYNC", 51, raising=False)
    monkeypatch.setattr(os, "fsync", lambda fd: events.append(("fsync", fd)))

    def full_flush(fd: int, operation: int) -> None:
        assert operation == 51
        events.append(("fullfsync", fd))
        raise OSError("drive flush failed")

    monkeypatch.setattr(fcntl, "fcntl", full_flush)
    with pytest.raises(OSError, match="drive flush failed"):
        files.fsync_file(17)
    assert events == [("fsync", 17), ("fullfsync", 17)]


@pytest.mark.parametrize("produce", [False, True])
def test_replay_drains_captured_heads_and_quiescence_counts_without_full_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, produce: bool
) -> None:
    async def run() -> None:
        mac = MacExecutor(
            paths=ExecutorPaths(tmp_path),
            revision="a" * 40,
            ssh_config=ExecutorSshConfig(
                "unused",
                1,
                "unused",
                tmp_path / "key",
                tmp_path / "hosts",
                SshAuthenticationMode.public_key,
            ),
            panels_server_url="http://unused",
        )
        # Requests below model a connected, accepted generation.
        mac._handshake_accepted = True
        for conversation in ("c_one", "c_one", "c_two", "c_two"):
            mac.spool.mint_envelope(
                conversation_id=conversation, kind=EnvelopeKind.cli_write, payload={}
            )
        visited: dict[str, list[int]] = {}
        producing = produce

        def forbid_full_inventory() -> None:
            raise AssertionError("routine consumer decoded the entire queue")

        async def request(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            assert kind is WireMessageKind.envelope
            conversation = str(payload["conversation_id"])
            sequence = payload["sequence"]
            assert isinstance(sequence, int)
            visited.setdefault(conversation, []).append(sequence)
            if producing:
                # Every receipt produces more work, so draining until empty cannot finish.
                mac.spool.mint_envelope(
                    conversation_id=conversation, kind=EnvelopeKind.cli_write, payload={}
                )
                if not mac.spool.has_pending("c_new"):
                    mac.spool.mint_envelope(
                        conversation_id="c_new", kind=EnvelopeKind.cli_write, payload={}
                    )
            receipt = AcceptanceReceipt(
                str(payload["sender_id"]),
                str(payload["submission_id"]),
                str(payload["payload_sha256"]),
                ReceiptDisposition.accepted,
                {},
                100,
            )
            return WireMessage(WireMessageKind.receipt, "row", as_object(receipt))

        monkeypatch.setattr(mac.spool, "pending", forbid_full_inventory)
        monkeypatch.setattr(mac, "_request", request)
        try:
            assert mac.registry._quiescence_counts() == (0, 0, 0, 4)
            assert not mac.registry.inventory(mac.identity, include_pending=False).queued_envelopes
            await asyncio.wait_for(mac._replay(), 2)
            assert visited == {"c_one": [1, 2], "c_two": [1, 2]}
            if produce:
                assert mac.spool.pending_sequence_ceilings() == {"c_new": 1, "c_one": 4, "c_two": 4}
                # A later pass captures the retained suffix and the new conversation.
                producing = False
                await asyncio.wait_for(mac._replay(), 2)
                assert visited == {"c_new": [1], "c_one": [1, 2, 3, 4], "c_two": [1, 2, 3, 4]}
            assert mac.registry._quiescence_counts() == (0, 0, 0, 0)
        finally:
            mac.spool.close()

    asyncio.run(run())
