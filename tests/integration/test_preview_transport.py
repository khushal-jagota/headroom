"""Synthetic Mac/VPS streams use real app WebSockets, TCP sockets and native events."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from tests.integration.test_executor_end_to_end import boot, eventually, public_ask_id

from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.core.server import http_status_for
from planner.previews.api import build_preview_router
from planner.previews.configuration import MAX_PREVIEW_STREAMS

pytestmark = pytest.mark.usefixtures("executor_transport_home")


def test_large_binary_http_body_and_shared_listener_leases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executor_transport_home: Path
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        body = bytes(range(256)) * 8192

        async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                headers = await reader.readuntil(b"\r\n\r\n")
                if headers.startswith(b"POST "):
                    # Exercise the chunk bound in both directions on the same host.
                    assert await reader.readexactly(len(body)) == body
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: "
                    + str(len(body)).encode()
                    + b"\r\n\r\n"
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(http, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        endpoint = harness.endpoint
        identity = endpoint.identity
        assert identity is not None
        assert endpoint._preview_forwards is not None
        try:
            async with endpoint.preview_forward(port, "t_scripted") as local:
                async with endpoint.preview_forward(port, "t_scripted") as shared:
                    assert shared == local
                    async with httpx.AsyncClient() as client:
                        response = await client.post(
                            f"http://127.0.0.1:{local}/", content=body, timeout=5
                        )
                        assert response.status_code == 200 and response.content == body
                    forwards = endpoint._preview_forwards
                    assert forwards is not None
                    held = next(iter(forwards.leases.values()))
                    async with httpx.AsyncClient() as client:
                        for _ in range(5):
                            response = await client.get(f"http://127.0.0.1:{local}/", timeout=5)
                            assert response.content == body
                            await eventually(
                                lambda: not held.tasks,
                                "completed stream UUID remained under held lease",
                            )
                assert endpoint._preview_forwards is not None
                assert len(endpoint._preview_forwards.leases) == 1
            assert not endpoint._preview_forwards.leases
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", local)
            forwards = endpoint._preview_forwards
            assert forwards is not None
            await eventually(
                lambda: not any(lease.tasks for lease in forwards.leases.values()),
                "preview streams survived completed request",
            )
            assert not harness.mac.spool.pending()
        finally:
            server.close()
            await server.wait_closed()
            await harness.close()

    asyncio.run(run())


def test_stalled_preview_keeps_executor_events_live_and_drop_cleans_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        accepted: list[asyncio.StreamWriter] = []

        def stalled(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            del reader
            accepted.append(writer)

        server = await asyncio.start_server(stalled, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        endpoint = harness.endpoint
        old_generation = harness.mac.identity.connection_generation
        try:
            with pytest.raises(ConnectionError, match="ended before request completion"):
                async with endpoint.preview_forward(port, "t_scripted") as local:
                    reader, writer = await asyncio.open_connection("127.0.0.1", local)
                    # Enough input to exhaust TCP buffering, leaving WebSocket flow control blocked.
                    writer.write(b"x" * (16 * 1024 * 1024))
                    drain = asyncio.create_task(writer.drain())
                    await eventually(lambda: bool(accepted), "Mac loopback dial absent")
                    await harness.native.raise_ask()
                    await harness.wait_ask_reachable()
                    assert await endpoint.answer_permission(
                        "c_scripted", public_ask_id(harness.ask), "allow", executor_ask=harness.ask
                    )
                    await eventually(
                        lambda: harness.native.answers == [("ask_permission", "allow")],
                        "permission answer stalled behind preview traffic",
                    )
                    assert endpoint._preview_forwards is not None
                    old = endpoint._preview_forwards
                    assert (
                        sum(len(lease.tasks) for lease in old.leases.values())
                        <= MAX_PREVIEW_STREAMS
                    )
                    assert endpoint.writer is not None
                    endpoint.writer.close()
                    await eventually(
                        lambda: old.closed and not old.leases,
                        "old preview generation survived link loss",
                    )
                    assert not old.leases
                    writer.transport.abort()
                    drain.cancel()
                    await asyncio.gather(drain, return_exceptions=True)
                    with contextlib.suppress(ConnectionError):
                        await reader.read()
            await eventually(
                lambda: (
                    endpoint.ready and harness.mac.identity.connection_generation > old_generation
                ),
                "executor did not reconnect",
            )
            assert endpoint._preview_forwards is not old
        finally:
            for peer in accepted:
                peer.transport.abort()
            server.close()
            await server.wait_closed()
            await harness.close()

    asyncio.run(run())


def test_last_lease_cancellation_stops_active_streams(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        accepted: list[asyncio.StreamWriter] = []

        def stalled(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            del reader
            accepted.append(writer)

        server = await asyncio.start_server(stalled, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        try:
            async with harness.endpoint.preview_forward(port, "t_scripted") as local:
                _, writer = await asyncio.open_connection("127.0.0.1", local)
                await eventually(lambda: bool(accepted), "Mac did not dial fixture")
            forwards = harness.endpoint._preview_forwards
            assert forwards is not None and not forwards.leases
            writer.close()
            await writer.wait_closed()
            mac_streams = harness.mac._preview_streams
            assert mac_streams is not None
            await eventually(
                lambda: not mac_streams.leases,
                "Mac stream survived lease cancellation",
            )
        finally:
            for peer in accepted:
                peer.transport.abort()
            server.close()
            await server.wait_closed()
            await harness.close()

    asyncio.run(run())


def test_old_session_cleanup_cannot_erase_ready_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        endpoint = harness.endpoint
        old = endpoint._preview_forwards
        assert old is not None
        original_close = old.close
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused_close() -> None:
            entered.set()
            await release.wait()
            await original_close()

        monkeypatch.setattr(old, "close", paused_close)
        generation = harness.mac.identity.connection_generation
        try:
            assert endpoint.writer is not None
            endpoint.writer.close()
            await asyncio.wait_for(entered.wait(), 2)
            await eventually(
                lambda: endpoint.ready and harness.mac.identity.connection_generation > generation,
                "replacement session waited for the old generation cleanup",
            )
            replacement = endpoint._preview_forwards
            writer = endpoint.writer
            assert replacement is not None and replacement is not old and writer is not None
            release.set()
            await eventually(lambda: old.closed, "old generation did not finish cleanup")
            assert endpoint.writer is writer and endpoint._preview_forwards is replacement
            assert endpoint.ready
        finally:
            release.set()
            await harness.close()

    asyncio.run(run())


def test_link_drop_after_partial_close_delimited_http_body_fails_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        peers: list[asyncio.StreamWriter] = []
        partial = b"body before executor link loss"

        async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            peers.append(writer)
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n" + partial)
            await writer.drain()

        server = await asyncio.start_server(http, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        try:
            with pytest.raises(ConnectionError, match="ended before request completion"):
                async with harness.endpoint.preview_forward(port, "t_scripted") as local:
                    async with httpx.AsyncClient() as client:
                        async with client.stream(
                            "GET", f"http://127.0.0.1:{local}/", timeout=3
                        ) as response:
                            chunks = response.aiter_raw()
                            assert response.status_code == 200
                            assert await anext(chunks) == partial
                            # Bytes demonstrably crossed the real app WebSockets.
                            assert harness.endpoint.writer is not None
                            harness.endpoint.writer.close()
                            assert b"".join([chunk async for chunk in chunks]) == b""
        finally:
            for peer in peers:
                peer.transport.abort()
            server.close()
            await server.wait_closed()
            await harness.close()

    asyncio.run(run())


def test_ready_preview_succeeds_while_held_prompt_resumption_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def run() -> None:
        harness = await boot(tmp_path, monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()
        resume = harness.system.resume_executor_held_prompts

        async def paused_resume(*, remote_ready: bool = False) -> None:
            entered.set()
            await release.wait()
            await resume(remote_ready=remote_ready)

        monkeypatch.setattr(harness.system, "resume_executor_held_prompts", paused_resume)

        async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(
                    b"HTTP/1.1 200 OK\r\nConnection: close\r\n"
                    b"Content-Length: 7\r\n\r\npreview"
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(http, "127.0.0.1", 0)
        port = int(server.sockets[0].getsockname()[1])
        app = FastAPI()
        app.state.executor_endpoint = harness.endpoint
        app.state.conn_factory = lambda: connect(harness.endpoint.db_path)
        app.include_router(build_preview_router())

        @app.exception_handler(PlannerError)
        async def failure(request: Request, error: PlannerError) -> JSONResponse:
            return JSONResponse(status_code=http_status_for(error.code), content=error.to_payload())

        ticket_id = next(iter(harness.mac.registry.children.values())).ticket_id
        assert ticket_id is not None
        try:
            assert harness.endpoint.writer is not None
            harness.endpoint.writer.close()
            # Hold the production await after VPS admission. No timing window or
            # retry determines whether a request crosses the ready ACK.
            await asyncio.wait_for(entered.wait(), 5)
            assert harness.endpoint.ready
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://localhost"
            ) as client:
                response = await client.get(
                    f"/dev/tickets/{ticket_id}/{port}/",
                    headers={"tailscale-user-login": "owner@example.com"},
                )
            assert response.status_code == 200, response.text
            assert response.content == b"preview"
        finally:
            release.set()
            server.close()
            await server.wait_closed()
            await harness.close()

    asyncio.run(run())
