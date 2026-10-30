"""Outbox confirmation and IPC caller lifetime, without the retired replay/pin machinery."""

from __future__ import annotations

import asyncio
import fcntl
import os
import sys
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
    IpcOperation,
    IpcRequest,
    JsonObject,
    OutboxRecord,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
)
from planner.executor.mac import MacExecutor, as_object


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


def test_ipc_receives_confirmation_before_source_publication_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        mac, request = ipc_writer(tmp_path, monkeypatch)
        source_work = mac.registry.source_work
        release = asyncio.Event()
        confirmed = asyncio.Event()

        async def publish_and_confirm(
            child: ChildReference,
            work: Callable[[], Awaitable[OutboxRecord]],
            *,
            new_work: bool,
        ) -> OutboxRecord:
            async def publish() -> OutboxRecord:
                row = await work()
                response = await mac.request(WireMessageKind.envelope, as_object(row.envelope))
                from pydantic import TypeAdapter

                mac.spool.acknowledge(
                    TypeAdapter(AcceptanceReceipt).validate_python(response.payload)
                )
                confirmed.set()
                await release.wait()
                return row

            return await source_work(child, publish, new_work=new_work)

        monkeypatch.setattr(mac.registry, "source_work", publish_and_confirm)
        try:
            response = await asyncio.wait_for(mac.handle_ipc(request), 2)
            assert confirmed.is_set() and response.result == {"landed": True}
            assert not mac._publications[request.request_id].task.done()
            release.set()
            await mac._publications[request.request_id].task
            await asyncio.sleep(0)
            assert not mac._publications and not mac.spool._waiters
        finally:
            release.set()
            mac.spool.close()

    asyncio.run(run())


@pytest.mark.parametrize("after_commit", [False, True])
def test_caller_cancellation_never_cancels_physical_add_or_later_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    async def run() -> None:
        mac, request = ipc_writer(tmp_path, monkeypatch)
        source_work = mac.registry.source_work
        entered, release = asyncio.Event(), asyncio.Event()

        async def controlled(
            child: ChildReference,
            work: Callable[[], Awaitable[OutboxRecord]],
            *,
            new_work: bool,
        ) -> OutboxRecord:
            async def publish() -> OutboxRecord:
                if after_commit:
                    row = await work()
                entered.set()
                await release.wait()
                return row if after_commit else await work()

            return await source_work(child, publish, new_work=new_work)

        monkeypatch.setattr(mac.registry, "source_work", controlled)
        task = asyncio.create_task(mac.handle_ipc(request))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            publication = mac._publications[request.request_id]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not publication.task.done()
            release.set()
            await publication.task
            row = mac.spool.lookup(request.request_id)
            assert row is not None and row.pending
            mac.spool.acknowledge(
                AcceptanceReceipt(
                    row.envelope.sender_id,
                    request.request_id,
                    row.envelope.payload_sha256,
                    ReceiptDisposition.accepted,
                    {"landed": True},
                    1,
                )
            )
            response = await mac.handle_ipc(
                replace(
                    request,
                    operation=IpcOperation.retry,
                    payload={"submission_id": request.request_id},
                )
            )
            assert response.result == {"landed": True}
            assert not mac.spool._waiters and not mac._publications
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            mac.spool.close()

    asyncio.run(run())
