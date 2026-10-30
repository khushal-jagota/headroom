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

from planner.execution_identity import ChildReference
from planner.executor import files
from planner.executor.configuration import ExecutorPaths
from planner.executor.mac_service import MacService
from planner.executor.protocol import (
    AcceptanceReceipt,
    IpcOperation,
    IpcRequest,
    JsonObject,
    OutboxRecord,
    ReceiptDisposition,
    WireMessage,
    WireMessageKind,
    as_object,
)


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


def ipc_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[MacService, IpcRequest]:
    mac = MacService(
        paths=ExecutorPaths(tmp_path),
        revision="a" * 40,
        executor_id="owner-mac",
        panels_server_url="https://unused",
    )
    mac.gateway._handshake_accepted = True
    reference = ChildReference(mac.identity.executor_incarnation, str(uuid4()), "c_one")
    child = SimpleNamespace(reference=reference, ticket_id=None, active_turn=None)
    monkeypatch.setattr(mac.runner, "authenticate", lambda *_: child)

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

    monkeypatch.setattr(mac.gateway, "request", accept)
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
        source_work = mac.runner.source_work
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
                response = await mac.gateway.request(
                    WireMessageKind.envelope, as_object(row.envelope)
                )
                from pydantic import TypeAdapter

                mac.outbox.acknowledge(
                    TypeAdapter(AcceptanceReceipt).validate_python(response.payload)
                )
                confirmed.set()
                await release.wait()
                return row

            return await source_work(child, publish, new_work=new_work)

        monkeypatch.setattr(mac.runner, "source_work", publish_and_confirm)
        try:
            response = await asyncio.wait_for(mac.gateway.handle(request), 2)
            assert confirmed.is_set() and response.result == {"landed": True}
            assert not mac.gateway._publications[request.request_id].task.done()
            release.set()
            await mac.gateway._publications[request.request_id].task
            await asyncio.sleep(0)
            assert not mac.gateway._publications and not mac.outbox._waiters
        finally:
            release.set()
            mac.outbox.close()

    asyncio.run(run())


@pytest.mark.parametrize("after_commit", [False, True])
def test_caller_cancellation_never_cancels_physical_add_or_later_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    async def run() -> None:
        mac, request = ipc_writer(tmp_path, monkeypatch)
        source_work = mac.runner.source_work
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

        monkeypatch.setattr(mac.runner, "source_work", controlled)
        task = asyncio.create_task(mac.gateway.handle(request))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            publication = mac.gateway._publications[request.request_id]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not publication.task.done()
            release.set()
            await publication.task
            row = mac.outbox.lookup(request.request_id)
            assert row is not None and row.pending
            mac.outbox.acknowledge(
                AcceptanceReceipt(
                    row.envelope.sender_id,
                    request.request_id,
                    row.envelope.payload_sha256,
                    ReceiptDisposition.accepted,
                    {"landed": True},
                    1,
                )
            )
            response = await mac.gateway.handle(
                replace(
                    request,
                    operation=IpcOperation.retry,
                    payload={"submission_id": request.request_id},
                )
            )
            assert response.result == {"landed": True}
            assert not mac.outbox._waiters and not mac.gateway._publications
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            mac.outbox.close()

    asyncio.run(run())
