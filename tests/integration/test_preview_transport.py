"""Real sockets prove the preview connector's transport boundaries."""

from __future__ import annotations

import asyncio
from typing import Any


def test_connector_sends_no_http_bytes_until_connected_socket_verification() -> None:
    """A rejected accepted-socket proof must happen before the HTTP request line."""
    from unittest.mock import AsyncMock, create_autospec

    import aiohttp

    from planner.previews.contracts import PreviewError, PreviewRecord, ProcessIdentity
    from planner.previews.service import PreviewService
    from planner.previews.transport import VerifiedConnector

    async def exercise() -> None:
        received: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

        async def accepted(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                received.set_result(await reader.read(1024))
            finally:
                writer.close()
                await writer.wait_closed()

        listener = await asyncio.start_server(accepted, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        service = create_autospec(PreviewService, instance=True)
        service.before_connection = AsyncMock()
        service.after_connection = AsyncMock(side_effect=PreviewError("Port was reused.", 410))
        record = PreviewRecord(
            "t_socket",
            "generation",
            "/synthetic",
            "https://panels.test",
            "epoch",
            0,
            "active",
            process=ProcessIdentity("boot", 1, 1, 1, port, "/synthetic"),
        )
        try:
            async with aiohttp.ClientSession(
                connector=VerifiedConnector(service, record)
            ) as client:
                try:
                    await client.get(f"http://127.0.0.1:{port}/must-not-arrive")
                except PreviewError as exc:
                    assert exc.status_code == 410
                else:
                    raise AssertionError("Rejected socket was used for an HTTP request")
            async with asyncio.timeout(1):
                assert await received == b""
            service.after_connection.assert_awaited_once()
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(exercise())


def test_websocket_redirect_never_connects_to_redirect_destination() -> None:

    import aiohttp

    from planner.previews.transport import PreviewClientSession

    async def exercise() -> None:
        destination_reached = False

        async def destination(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            nonlocal destination_reached
            destination_reached = True
            writer.close()
            await writer.wait_closed()

        target = await asyncio.start_server(destination, "127.0.0.1", 0)
        port = target.sockets[0].getsockname()[1]

        async def redirect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                f"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:{port}/leak\r\n"
                "Content-Length: 0\r\n\r\n".encode()
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        source = await asyncio.start_server(redirect, "127.0.0.1", 0)
        source_port = source.sockets[0].getsockname()[1]
        try:
            async with PreviewClientSession() as session:
                try:
                    await session.ws_connect(f"http://127.0.0.1:{source_port}/generation/")
                except aiohttp.WSServerHandshakeError as exc:
                    assert exc.status == 302
                else:
                    raise AssertionError("WebSocket redirect unexpectedly succeeded")
            assert not destination_reached
        finally:
            source.close()
            target.close()
            await source.wait_closed()
            await target.wait_closed()

    asyncio.run(exercise())


def test_complete_body_reaches_eof_after_parser_flow_control() -> None:
    from planner.previews.transport import HeaderBoundedProtocol

    class ObservedTransport(asyncio.Transport):
        def __init__(self) -> None:
            self.pauses = 0
            self.resumes = 0
            self.closed = False

        def get_extra_info(self, name: str, default: Any = None) -> Any:
            return default

        def pause_reading(self) -> None:
            self.pauses += 1

        def resume_reading(self) -> None:
            self.resumes += 1

        def is_closing(self) -> bool:
            return self.closed

        def close(self) -> None:
            self.closed = True

    async def exercise() -> None:
        protocol = HeaderBoundedProtocol(asyncio.get_running_loop())
        transport = ObservedTransport()
        protocol.connection_made(transport)
        protocol.set_response_params(read_bufsize=65536, auto_decompress=False)
        initial_pauses, initial_resumes = transport.pauses, transport.resumes
        body = b"x" * 184031
        headers = (
            f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        try:
            protocol.data_received(headers + body)
            async with asyncio.timeout(1):
                _, payload = await protocol.read()
                received = await payload.readexactly(len(body))
            eof_after_drain = payload.is_eof()
        finally:
            protocol.connection_lost(None)

        assert transport.pauses > initial_pauses
        assert transport.resumes > initial_resumes
        assert received == body
        assert eof_after_drain
        assert payload.at_eof()
        assert payload.exception() is None

    asyncio.run(exercise())


def test_upstream_header_budget_applies_before_parser_allocation() -> None:
    from unittest.mock import create_autospec

    from planner.previews import config
    from planner.previews.contracts import PreviewError, PreviewRecord, ProcessIdentity
    from planner.previews.service import PreviewService
    from planner.previews.transport import PreviewClientSession, VerifiedConnector

    async def exercise() -> None:
        for wire in [
            b"HTTP/1.1 200 OK\r\nX-Never-Terminated: " + b"x" * config.HEADER_BYTES,
            b"HTTP/1.1 200 OK\r\n" + b"X-Small: value\r\n" * 2500 + b"\r\n",
            b"HTTP/1.1 103 Early Hints\r\n\r\n" * 1500,
            b"HTTP/1.1 200 OK\r\nContent-Length: 65536\r\n\r\n" + b"x" * 65536,
        ]:

            async def application(
                reader: asyncio.StreamReader, writer: asyncio.StreamWriter, payload: bytes = wire
            ) -> None:
                try:
                    await reader.readuntil(b"\r\n\r\n")
                    writer.write(payload)
                    await writer.drain()
                except ConnectionError:
                    pass
                finally:
                    writer.close()
                    await writer.wait_closed()

            listener = await asyncio.start_server(application, "127.0.0.1", 0)
            port = listener.sockets[0].getsockname()[1]
            service = create_autospec(PreviewService, instance=True)
            record = PreviewRecord(
                "t_headers",
                "generation",
                "/synthetic",
                "https://panels.test",
                "epoch",
                0,
                "active",
                process=ProcessIdentity("boot", 1, 1, 1, port, "/synthetic"),
            )
            try:
                async with PreviewClientSession(
                    connector=VerifiedConnector(service, record)
                ) as client:
                    try:
                        response = await client.get(f"http://127.0.0.1:{port}/")
                    except PreviewError as exc:
                        assert exc.status_code == 502
                        assert b"Content-Length: 65536" not in wire
                    else:
                        assert await response.read() == b"x" * 65536
                        response.close()
            finally:
                listener.close()
                await listener.wait_closed()

    asyncio.run(exercise())


def test_eager_peer_is_paused_while_socket_evidence_is_pending() -> None:
    from unittest.mock import AsyncMock, create_autospec, patch

    from planner.previews.contracts import PreviewRecord, ProcessIdentity
    from planner.previews.service import PreviewService
    from planner.previews.transport import (
        HeaderBoundedProtocol,
        PreviewClientSession,
        VerifiedConnector,
    )

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        evidence_pending, release = asyncio.Event(), asyncio.Event()
        protocols: list[HeaderBoundedProtocol] = []
        handlers: set[asyncio.Task[Any]] = set()

        def protocol_factory(loop: asyncio.AbstractEventLoop) -> HeaderBoundedProtocol:
            protocol = HeaderBoundedProtocol(loop)
            protocols.append(protocol)
            return protocol

        async def eager(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            assert task is not None
            handlers.add(task)
            try:
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 16777216\r\n\r\n")
                for _ in range(256):
                    writer.write(b"x" * 65536)
                    await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                handlers.discard(task)

        listener = await asyncio.start_server(eager, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        service = create_autospec(PreviewService, instance=True)

        async def prove(_record: PreviewRecord, _local_port: int) -> None:
            evidence_pending.set()
            await release.wait()

        service.after_connection = AsyncMock(side_effect=prove)
        record = PreviewRecord(
            "t_eager",
            "generation",
            "/synthetic",
            "https://panels.test",
            "epoch",
            0,
            "active",
            process=ProcessIdentity("boot", 1, 1, 1, port, "/synthetic"),
        )
        try:
            with patch("planner.previews.transport.HeaderBoundedProtocol", protocol_factory):
                async with PreviewClientSession(
                    connector=VerifiedConnector(service, record)
                ) as client:
                    request = loop.create_task(client._request("GET", f"http://127.0.0.1:{port}/"))
                    try:
                        async with asyncio.timeout(1):
                            await evidence_pending.wait()
                        for _ in range(10):
                            await asyncio.sleep(0.005)
                        assert protocols[0]._reading_paused
                        assert protocols[0]._tail == b""
                        assert not protocols[0].header_bytes
                        release.set()
                        response = await request
                        assert await response.content.read(1) == b"x"
                        response.close()
                    finally:
                        release.set()
                        request.cancel()
                        await asyncio.gather(request, return_exceptions=True)
        finally:
            listener.close()
            await listener.wait_closed()
            for task in tuple(handlers):
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)

    asyncio.run(exercise())
