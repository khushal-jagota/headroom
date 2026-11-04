"""Private adapters with exact fixtures and retained-core producer composition.

Synthetic SQLite files supply all storage. Actual producer cases use a temporary
runtime lease and normal transactions; no server, agent home, provider or live DB runs.
These cases do not establish public consumer cutover or whole-runtime acceptance.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from starlette.types import Message

from planner.conversation.runtime_composition import SignalRuntimeComposition
from planner.core.sse import open_change_stream_count
from planner.navigation import snapshot as navigation_snapshot
from planner.navigation.delivery import DeliveryRuntime
from planner.navigation.delivery_authority import DeliveryRead, DeliveryUnavailable
from planner.navigation.signal_runtime import SignalNavigationBinding


@dataclass
class _ActualBinding:
    composition: SignalRuntimeComposition
    delivery: DeliveryRuntime
    binding: SignalNavigationBinding
    path: Path


@pytest.fixture
def actual_binding(tmp_path: Path) -> Iterator[_ActualBinding]:
    path = tmp_path / "actual-signal.db"
    composition = SignalRuntimeComposition(str(path), 1000)
    composition.bootstrap()
    composition.coordinator.bind_runtime(lambda identity: None)
    conn = composition.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,"
            "created_at) "
            "VALUES('actual','codex','/synthetic','full',0)"
        )
        conn.commit()
    finally:
        conn.close()
    composition.initialize()
    delivery = DeliveryRuntime(path)
    binding = SignalNavigationBinding(composition.coordinator, delivery)
    try:
        yield _ActualBinding(composition, delivery, binding, path)
    finally:
        binding.close()
        composition.close()


def test_actual_domain_callback_invalidates_threaded_snapshot_across_reattachment(
    actual_binding: _ActualBinding, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.navigation import delivery

    host = actual_binding
    host.binding.attach()
    pinned = threading.Event()
    release = threading.Event()
    original = navigation_snapshot

    def blocked(conn: sqlite3.Connection) -> object:
        result = original(conn)
        pinned.set()
        assert release.wait(3), "snapshot worker was not released"
        return result

    monkeypatch.setattr(delivery, "snapshot", blocked)

    async def run() -> None:
        pending = asyncio.create_task(host.delivery.current_snapshot())
        try:
            assert await asyncio.to_thread(pinned.wait, 3)
            host.binding.close()
            host.binding.attach()
        finally:
            release.set()
        with pytest.raises(DeliveryUnavailable):
            await pending
        await host.delivery.current_snapshot()

    asyncio.run(run())


def test_actual_domain_callback_cancels_admitted_send_and_rejects_old_read(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    host.binding.attach()

    async def run() -> None:
        read = host.delivery.admission.capture()
        entered = asyncio.Event()
        calls: list[Message] = []

        async def send(message: Message) -> None:
            calls.append(message)
            entered.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(
            host.delivery.admission.send(read, send, {"type": "http.response.body", "body": b"old"})
        )
        await entered.wait()
        host.binding.close()
        host.binding.attach()
        with pytest.raises(asyncio.CancelledError):
            await pending
        with pytest.raises(DeliveryUnavailable):
            await host.delivery.admission.send(
                read, send, {"type": "http.response.body", "body": b"late"}
            )
        assert len(calls) == 1

    asyncio.run(run())


def test_actual_domain_callback_closes_pending_stream_across_reattachment(
    actual_binding: _ActualBinding,
) -> None:
    host = actual_binding
    host.binding.attach()

    async def run() -> None:
        baseline = open_change_stream_count()
        value, read = await host.delivery.current_snapshot()
        assert value["mode"] == "current" and isinstance(read, DeliveryRead)
        host.delivery.ready = True
        stream = host.delivery.stream(value["cursor"], read, value["authority"])
        pending = asyncio.ensure_future(anext(stream))
        try:
            for _ in range(50):
                if open_change_stream_count() > baseline:
                    break
                await asyncio.sleep(0)
            assert open_change_stream_count() == baseline + 1
            host.binding.close()
            host.binding.attach()
            with pytest.raises(StopAsyncIteration):
                await asyncio.wait_for(pending, 3)
            assert open_change_stream_count() == baseline
            await host.delivery.current_snapshot()
        finally:
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            host.delivery.ready = False

    asyncio.run(run())
