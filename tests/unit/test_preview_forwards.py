"""Preview admission and cleanup protect the SSH generation boundary."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import asyncssh
import pytest

from planner.executor.contracts import JsonObject, WireMessage, WireMessageKind
from planner.previews.configuration import MAX_PREVIEW_STREAMS
from planner.previews.forward import MacPreviewForwards, PreviewForwards


def test_mac_limits_channels_at_admission_before_coroutines_start(tmp_path: Path) -> None:
    async def exercise() -> None:
        connection = Mock(spec=asyncssh.SSHClientConnection)
        listener = Mock(spec=asyncssh.SSHListener)
        listener.wait_closed = AsyncMock()
        connection.start_unix_server = AsyncMock(return_value=listener)
        forwards = MacPreviewForwards(connection, tmp_path, lambda: True)
        path = str(tmp_path / ("a" * 32))
        await forwards.bind(path, 8080)
        factory = connection.start_unix_server.call_args.args[0]
        # Accept a burst before any coroutine can enter its first instruction.
        for _ in range(MAX_PREVIEW_STREAMS):
            factory()
        with pytest.raises(asyncssh.ChannelOpenError):
            factory()
        await forwards.release(path)
        with pytest.raises(asyncssh.ChannelOpenError):
            factory()
        with pytest.raises(ValueError, match="unknown"):
            await forwards.release(path)
        listener.close.assert_called_once()

    asyncio.run(exercise())


def test_mac_refuses_untrusted_paths_ports_and_stale_generation(tmp_path: Path) -> None:
    async def exercise() -> None:
        connection = Mock(spec=asyncssh.SSHClientConnection)
        current = True
        forwards = MacPreviewForwards(connection, tmp_path, lambda: current)
        for path, port in [
            (str(tmp_path / "../elsewhere"), 8080),
            (str(tmp_path / ("a" * 32)), True),
            (str(tmp_path / ("a" * 32)), 65536),
        ]:
            with pytest.raises(ValueError):
                await forwards.bind(path, port)
        current = False
        with pytest.raises(ValueError):
            await forwards.bind(str(tmp_path / ("a" * 32)), 8080)
        connection.start_unix_server.assert_not_called()

    asyncio.run(exercise())


def test_lost_bind_reply_revokes_remote_lease(tmp_path: Path) -> None:
    async def exercise() -> None:
        calls: list[WireMessageKind] = []

        async def request(kind: WireMessageKind, payload: JsonObject) -> WireMessage:
            calls.append(kind)
            if kind is WireMessageKind.preview_bind:
                raise TimeoutError("lost bind reply")
            return WireMessage(WireMessageKind.preview_bound, "release", {})

        forwards = PreviewForwards(tmp_path, request, lambda: True)
        with pytest.raises(TimeoutError):
            async with forwards.lease(8080, "ticket"):
                pytest.fail("failed bind entered the lease")
        assert calls == [WireMessageKind.preview_bind, WireMessageKind.preview_release]
        assert not forwards.leases

    asyncio.run(exercise())


def test_socket_cleanup_refuses_replacement_or_non_socket(tmp_path: Path) -> None:
    forwards = PreviewForwards(tmp_path, AsyncMock(), lambda: True)
    path = tmp_path / "socket"
    with socket.socket(socket.AF_UNIX) as original:
        original.bind(str(path))
        info = path.lstat()
        identity = info.st_dev, info.st_ino
        path.unlink()
        path.write_bytes(b"unrelated replacement")
        with pytest.raises(ValueError, match="unsafe"):
            forwards._remove_socket(path, identity)
        assert path.read_bytes() == b"unrelated replacement"


def test_mac_channel_uses_only_loopback_and_release_closes_active_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        connection = Mock(spec=asyncssh.SSHClientConnection)
        listener = Mock(spec=asyncssh.SSHListener)
        listener.wait_closed = AsyncMock()
        connection.start_unix_server = AsyncMock(return_value=listener)
        reader = asyncio.StreamReader()
        writer = Mock(spec=asyncssh.SSHWriter)
        local_reader = asyncio.StreamReader()
        local_writer = Mock(spec=asyncio.StreamWriter)
        local_writer.drain = AsyncMock()
        dial = AsyncMock(return_value=(local_reader, local_writer))
        monkeypatch.setattr(asyncio, "open_connection", dial)
        forwards = MacPreviewForwards(connection, tmp_path, lambda: True)
        path = str(tmp_path / ("a" * 32))
        await forwards.bind(path, 8080)
        factory = connection.start_unix_server.call_args.args[0]
        task = asyncio.create_task(factory()(reader, writer))
        for _ in range(10):
            await asyncio.sleep(0)
            if forwards.leases[path].tasks and dial.called:
                break
        dial.assert_awaited_once_with("127.0.0.1", 8080)
        await asyncio.wait_for(forwards.release(path), 1)
        assert task.done()
        assert not forwards._accepted_channels
        writer.close.assert_called()
        local_writer.close.assert_called()

    asyncio.run(exercise())


def test_idle_preview_stream_stops_at_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    from planner.previews import forward

    async def exercise() -> None:
        reader = asyncio.StreamReader()
        writer = Mock(spec=asyncio.StreamWriter)
        monkeypatch.setattr(forward, "PREVIEW_IDLE_TIMEOUT_SECONDS", 0.001)
        with pytest.raises(TimeoutError):
            await forward.copy_streams(reader, writer)
        writer.write.assert_not_called()

    asyncio.run(exercise())
