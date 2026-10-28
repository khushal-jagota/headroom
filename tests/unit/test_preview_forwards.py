"""Preview admission and cleanup protect generation-bound WebSocket streams."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from planner.executor.contracts import JsonObject, PreviewPeer, WireMessage, WireMessageKind
from planner.previews.configuration import MAX_PREVIEW_STREAMS, PREVIEW_CHUNK_BYTES
from planner.previews.forward import MacPreviewForwards, PreviewForwards


@asynccontextmanager
async def blocked_stream(stream_id: str) -> AsyncIterator[PreviewPeer]:
    del stream_id
    await asyncio.Event().wait()
    yield cast(PreviewPeer, None)


def test_mac_limits_pending_streams_before_coroutines_start() -> None:
    async def exercise() -> None:
        forwards = MacPreviewForwards(blocked_stream, lambda: True)
        lease_id = "a" * 32
        await forwards.bind(lease_id, 8080)
        for index in range(MAX_PREVIEW_STREAMS):
            forwards.open(lease_id, f"{index:032x}", 8080)
        with pytest.raises(ValueError, match="unavailable"):
            forwards.open(lease_id, "b" * 32, 8080)
        await forwards.release(lease_id)
        assert not forwards._streams
        with pytest.raises(ValueError):
            forwards.open(lease_id, "c" * 32, 8080)
        with pytest.raises(ValueError, match="unknown"):
            await forwards.release(lease_id)

    asyncio.run(exercise())


def test_mac_refuses_untrusted_ids_changed_ports_and_stale_generation() -> None:
    async def exercise() -> None:
        current = True
        forwards = MacPreviewForwards(blocked_stream, lambda: current)
        for lease_id, port in [("../elsewhere", 8080), ("a" * 32, True), ("a" * 32, 65536)]:
            with pytest.raises(ValueError):
                await forwards.bind(lease_id, port)
        await forwards.bind("a" * 32, 8080)
        for lease_id, stream_id, port in [
            ("a" * 32, "b" * 32, 8081),
            ("a" * 32, "../elsewhere", 8080),
            ("c" * 32, "b" * 32, 8080),
        ]:
            with pytest.raises(ValueError):
                forwards.open(lease_id, stream_id, port)
        current = False
        with pytest.raises(ValueError):
            forwards.open("a" * 32, "b" * 32, 8080)
        with pytest.raises(ValueError):
            await forwards.bind("c" * 32, 8080)
        await forwards.close()

    asyncio.run(exercise())


def test_lost_bind_reply_revokes_remote_lease() -> None:
    async def exercise() -> None:
        calls: list[tuple[WireMessageKind, JsonObject]] = []

        async def request(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            calls.append((kind, payload))
            if kind is WireMessageKind.preview_bind:
                raise TimeoutError("lost bind reply")
            return WireMessage(WireMessageKind.preview_bound, "release", {})

        forwards = PreviewForwards(request, lambda: True)
        with pytest.raises(TimeoutError):
            async with forwards.lease(8080, "ticket"):
                pytest.fail("failed bind entered the lease")
        assert [kind for kind, _ in calls] == [
            WireMessageKind.preview_bind,
            WireMessageKind.preview_release,
        ]
        assert calls[0][1]["lease_id"] == calls[1][1]["lease_id"]
        assert not forwards.leases

    asyncio.run(exercise())


def test_server_reserves_capacity_before_tasks_start_and_cleans_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        request = AsyncMock(return_value=WireMessage(WireMessageKind.preview_bound, "bind", {}))
        server = Mock(spec=asyncio.Server)
        server.sockets = [Mock()]
        server.sockets[0].getsockname.return_value = ("127.0.0.1", 42000)
        server.wait_closed = AsyncMock()
        start = AsyncMock(return_value=server)
        monkeypatch.setattr(asyncio, "start_server", start)
        forwards = PreviewForwards(request, lambda: True)
        async with forwards.lease(8080, "ticket"):
            accept = start.call_args.args[0]
            peers = [Mock(spec=asyncio.StreamWriter) for _ in range(MAX_PREVIEW_STREAMS + 1)]
            for writer in peers:
                accept(asyncio.StreamReader(), writer)
            assert len(forwards._streams) == MAX_PREVIEW_STREAMS
            peers[-1].close.assert_called_once()
            assert len(next(iter(forwards.leases.values())).tasks) == MAX_PREVIEW_STREAMS
        assert not forwards._streams and not forwards.leases
        server.close.assert_called_once()

    asyncio.run(exercise())


def test_mac_stream_uses_only_fixed_loopback_and_release_closes_active_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        local_reader = asyncio.StreamReader()
        local_writer = Mock(spec=asyncio.StreamWriter)
        local_writer.drain = AsyncMock()
        dial = AsyncMock(return_value=(local_reader, local_writer))
        monkeypatch.setattr(asyncio, "open_connection", dial)
        entered, closed = asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def open_stream(stream_id: str) -> AsyncIterator[PreviewPeer]:
            assert stream_id == "b" * 32
            peer = Mock()

            async def receive(max_bytes: int) -> bytes:
                await asyncio.Event().wait()
                return b""

            peer.receive_bytes = AsyncMock(side_effect=receive)
            peer.send_bytes = AsyncMock()
            peer.aclose = AsyncMock()
            entered.set()
            try:
                yield peer
            finally:
                closed.set()

        forwards = MacPreviewForwards(open_stream, lambda: True)
        await forwards.bind("a" * 32, 8080)
        forwards.open("a" * 32, "b" * 32, 8080)
        await asyncio.wait_for(entered.wait(), 1)
        dial.assert_awaited_once_with("127.0.0.1", 8080)
        await asyncio.wait_for(forwards.release("a" * 32), 1)
        assert not forwards._streams and closed.is_set()
        local_writer.close.assert_called()

    asyncio.run(exercise())


def test_unknown_and_duplicate_rendezvous_refuse_peer() -> None:
    from planner.previews.forward import ForwardLease, PendingStream

    async def exercise() -> None:
        forwards = PreviewForwards(AsyncMock(), lambda: True)
        peer = Mock()
        peer.aclose = AsyncMock()
        with pytest.raises(ValueError, match="unknown"):
            await forwards.attach("a" * 32, peer)
        lease = ForwardLease(Mock(spec=asyncio.Server), 42000, "b" * 32)
        stream = PendingStream(lease, asyncio.get_running_loop().create_future())
        forwards._streams["a" * 32] = stream
        task = asyncio.create_task(forwards.attach("a" * 32, peer))
        assert await stream.peer is peer
        with pytest.raises(ValueError, match="consumed"):
            await forwards.attach("a" * 32, peer)
        stream.done.set()
        await task
        lease.closed = True
        with pytest.raises(ValueError):
            await forwards.attach("a" * 32, peer)

    asyncio.run(exercise())


def test_preview_chunks_backpressure_and_idle_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from planner.previews import forward

    async def exercise() -> None:
        reader = asyncio.StreamReader()
        writer = Mock(spec=asyncio.StreamWriter)
        peer = Mock()
        blocked = asyncio.Event()

        async def send(data: bytes) -> None:
            await blocked.wait()

        async def receive(max_bytes: int) -> bytes:
            await blocked.wait()
            return b""

        peer.send_bytes = AsyncMock(side_effect=send)
        peer.receive_bytes = AsyncMock(side_effect=receive)
        peer.aclose = AsyncMock()
        reader.feed_data(b"x" * (PREVIEW_CHUNK_BYTES * 3))
        task = asyncio.create_task(forward._bridge_peer(reader, writer, peer))
        for _ in range(10):
            await asyncio.sleep(0)
            if peer.send_bytes.called:
                break
        assert peer.send_bytes.call_count == 1
        assert len(peer.send_bytes.call_args.args[0]) == PREVIEW_CHUNK_BYTES
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        monkeypatch.setattr(forward, "PREVIEW_IDLE_TIMEOUT_SECONDS", 0.001)
        with pytest.raises(TimeoutError):
            await forward.copy_streams(asyncio.StreamReader(), writer)

    asyncio.run(exercise())
