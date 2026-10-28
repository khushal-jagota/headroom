"""Transfer lifetime and Mac durability boundaries around the SQLite outbox."""

from __future__ import annotations

import asyncio
import fcntl
import os
import sqlite3
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from planner.executor import files
from planner.executor.configuration import ExecutorPaths
from planner.executor.contracts import (
    AcceptanceReceipt,
    ChildReference,
    EnvelopeKind,
    FileScope,
    IpcOperation,
    IpcRequest,
    JournalPublication,
    JsonObject,
    ReceiptDisposition,
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
            executor_id="owner-mac",
            panels_server_url="https://unused",
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
            executor_id="owner-mac",
            panels_server_url="https://unused",
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


def ipc_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MacExecutor, IpcRequest]:
    mac = MacExecutor(
        paths=ExecutorPaths(tmp_path),
        revision="a" * 40,
        executor_id="owner-mac",
        panels_server_url="https://unused",
    )
    mac._handshake_accepted = True
    reference = ChildReference(mac.identity.executor_incarnation, str(uuid4()), "c_one")
    child = SimpleNamespace(reference=reference, ticket_id=None, active_turn=None)
    monkeypatch.setattr(mac.registry, "authenticate", lambda *_: child)

    async def accept(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
        assert kind is WireMessageKind.envelope
        return WireMessage(
            WireMessageKind.receipt,
            "accepted",
            as_object(
                AcceptanceReceipt(
                    str(payload["sender_id"]),
                    str(payload["submission_id"]),
                    str(payload["payload_sha256"]),
                    ReceiptDisposition.accepted,
                    {"landed": True},
                    100,
                )
            ),
        )

    monkeypatch.setattr(mac, "_request", accept)
    return mac, IpcRequest(
        "original-submission",
        IpcOperation.write,
        reference.child_id,
        "capability",
        {"operation": "ticket.note", "arguments": {}},
    )


def test_ipc_receives_acknowledgment_before_source_publication_returns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        mac, request = ipc_writer(tmp_path, monkeypatch)
        source_work = mac.registry.source_work
        acknowledged = False

        async def publish_and_ack(
            child: ChildReference,
            work: Callable[[], Awaitable[JournalPublication]],
            *,
            new_work: bool,
        ) -> JournalPublication:
            async def publish() -> JournalPublication:
                nonlocal acknowledged
                envelope = await work()
                # Actual receipt validation and durable acknowledgment happen while
                # source_work still owns publication and has not returned its row.
                await mac._replay_envelope(envelope.envelope)
                acknowledged = True
                assert not mac.spool.has_pending()
                return envelope

            return await source_work(child, publish, new_work=new_work)

        monkeypatch.setattr(mac.registry, "source_work", publish_and_ack)
        try:
            response = await asyncio.wait_for(mac.handle_ipc(request), 2)
            assert acknowledged
            assert response.result == {"landed": True}
            assert response.error is None and response.queued is None
            if request.request_id in mac._publications:
                await mac._publications[request.request_id].task
                await asyncio.sleep(0)  # Let the settled task release its receipt tracker.
            assert not mac._receipts
        finally:
            mac.spool.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "outcome", ["failure", "publication_timeout", "timeout", "cancel_publication", "cancel_receipt"]
)
def test_ipc_waiter_cleanup_preserves_durable_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    async def run() -> None:
        from planner.executor import mac as module

        mac, request = ipc_writer(tmp_path, monkeypatch)
        source_work = mac.registry.source_work
        entered, release = asyncio.Event(), asyncio.Event()
        receipt_wait = asyncio.Event()
        receipt: asyncio.Future[AcceptanceReceipt] | None = None
        clock_offset = 0.0

        class ClockProxy:
            def monotonic(self) -> float:
                return time.monotonic() + clock_offset

            def __getattr__(self, name: str) -> object:
                return getattr(time, name)

        class AsyncioProxy:
            def __getattr__(self, name: str) -> object:
                return getattr(asyncio, name)

            async def wait(
                self,
                futures: tuple[asyncio.Future[object], ...],
                *,
                timeout: float | None = None,
                return_when: str = asyncio.ALL_COMPLETED,
            ) -> tuple[set[asyncio.Future[object]], set[asyncio.Future[object]]]:
                if len(futures) == 1 and futures[0] is receipt:
                    assert publication.task.done()
                    assert receipt is not None and not receipt.done()
                    assert not task.done()
                    if outcome == "timeout":
                        assert timeout == 0
                    receipt_wait.set()
                return await asyncio.wait(futures, timeout=timeout, return_when=return_when)

        async def controlled(
            child: ChildReference,
            work: Callable[[], Awaitable[JournalPublication]],
            *,
            new_work: bool,
        ) -> JournalPublication:
            async def publish() -> JournalPublication:
                nonlocal clock_offset
                entered.set()
                await release.wait()
                if outcome == "failure":
                    raise ValueError("publication refused")
                if outcome == "publication_timeout":
                    raise TimeoutError("publication timed out")
                row = await work()
                if outcome == "timeout":
                    # Expire only the observer's clock, after physical publication.
                    clock_offset = 60
                return row

            return await source_work(child, publish, new_work=new_work)

        monkeypatch.setattr(mac.registry, "source_work", controlled)
        monkeypatch.setattr(module, "CLI_RESULT_TIMEOUT_SECONDS", 30)
        monkeypatch.setattr(module, "asyncio", AsyncioProxy())
        if outcome == "timeout":
            monkeypatch.setattr(module, "time", ClockProxy())
        request = replace(request, response_deadline=time.monotonic() + 30)
        task = asyncio.create_task(mac.handle_ipc(request))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            publication = mac._publications[request.request_id]
            receipt = mac._receipts[request.request_id]
            if outcome == "cancel_publication":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not publication.task.done()
                assert not receipt.done()
                assert mac._publications[request.request_id] is publication
                assert mac._receipts[request.request_id] is receipt
                assert mac.registry._source_work == {
                    ChildReference(
                        mac.identity.executor_incarnation, str(request.child_id), "c_one"
                    ): 1
                }
                release.set()
            else:
                release.set()
                if outcome == "cancel_receipt":
                    await asyncio.wait_for(receipt_wait.wait(), 5)
                    assert publication.task.done()
                    assert publication.task.result().pending
                    assert not receipt.done()
                    assert not task.done()
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                elif outcome == "publication_timeout":
                    with pytest.raises(TimeoutError, match="publication timed out"):
                        await task
                else:
                    response = await task
                    if outcome == "failure":
                        assert response.error is not None
                        assert response.error["message"] == "publication refused"
                    else:
                        assert receipt_wait.is_set()
                        assert response.queued is not None
                        assert response.queued.submission_id == request.request_id
                        assert response.error is None and response.unconfirmed is None
            # Keep the actual task even after its callbacks remove the registry entry.
            await asyncio.gather(publication.task, return_exceptions=True)
            await asyncio.sleep(0)
            assert receipt.cancelled()
            assert not mac._publications
            assert not mac._receipts
            assert not mac.registry._source_work
            if outcome in ("failure", "publication_timeout"):
                assert not mac.spool.has_pending()
            else:
                row = publication.task.result()
                assert row.pending
                assert row.envelope.submission_id == request.request_id
                assert row.envelope.payload["operation"] == "ticket.note"
                assert row.envelope.payload["arguments"] == {}
                assert mac.spool.pending() == (row.envelope,)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if mac._publications:
                await asyncio.gather(
                    *(row.task for row in mac._publications.values()), return_exceptions=True
                )
            await asyncio.sleep(0)
            mac.spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("changed_content", [False, True])
def test_concurrent_duplicate_cannot_replace_original_receipt_waiter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_content: bool,
) -> None:
    async def run() -> None:
        import time
        from dataclasses import replace

        mac, request = ipc_writer(tmp_path, monkeypatch)
        source_work = mac.registry.source_work
        entered, release = asyncio.Event(), asyncio.Event()

        async def controlled(
            child: ChildReference,
            work: Callable[[], Awaitable[JournalPublication]],
            *,
            new_work: bool,
        ) -> JournalPublication:
            async def publish() -> JournalPublication:
                row = await work()
                entered.set()
                await release.wait()
                await mac._replay_envelope(row.envelope)
                return row

            return await source_work(child, publish, new_work=new_work)

        monkeypatch.setattr(mac.registry, "source_work", controlled)
        original = asyncio.create_task(mac.handle_ipc(request))
        try:
            await entered.wait()
            owned = mac._receipts[request.request_id]
            duplicate = (
                replace(request, payload={"operation": "different", "arguments": {}})
                if changed_content
                else request
            )
            response = await mac.handle_ipc(
                replace(duplicate, response_deadline=time.monotonic() + 0.01)
            )
            assert response.result is None
            if changed_content:
                assert response.error is not None and response.error["code"] == "validation"
            else:
                assert response.error is None and response.unconfirmed is not None
                assert response.unconfirmed.submission_id == request.request_id
                assert response.unconfirmed.publication == "preparing"
            assert mac._receipts[request.request_id] is owned and not owned.done()
            assert len(mac.spool.pending()) == 1
            release.set()
            result = await asyncio.wait_for(original, 2)
            assert result.result == {"landed": True} and result.error is None
            if request.request_id in mac._publications:
                await mac._publications[request.request_id].task
                await asyncio.sleep(0)  # Let the settled task release its receipt tracker.
            assert not mac._receipts and not mac.spool.has_pending()
        finally:
            release.set()
            await asyncio.gather(original, return_exceptions=True)
            mac.spool.close()

    asyncio.run(run())
