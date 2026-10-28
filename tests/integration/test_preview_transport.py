"""Real socket saturation must not monopolise the ordinary Panels request loop."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import httpx
from tests.support.server import ServerHandle

APP = """
import asyncio
from pathlib import Path
from aiohttp import web
async def application(request):
    if request.path.endswith('/sized'):
        return web.Response(body=b'x' * 8192, content_type='text/plain')
    response = web.StreamResponse()
    await response.prepare(request)
    try:
        if request.path.endswith('/slow'):
            await response.write(b'start')
            await asyncio.sleep(60)
        else:
            for _ in range(256):
                await response.write(b'x' * 65536)
    except (ConnectionError, asyncio.CancelledError):
        pass
    return response
async def main():
    app=web.Application()
    app.router.add_route('*', '/{path:.*}', application)
    runner=web.AppRunner(app)
    await runner.setup()
    site=web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    Path('port').write_text(str(site._server.sockets[0].getsockname()[1]))
    await asyncio.Event().wait()
asyncio.run(main())
"""


def _json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    response = client.request(method, url, **kwargs)
    assert response.status_code in {200, 201}, response.text
    return response.json()  # type: ignore[no-any-return]


def test_saturation_preserves_ticket_reads_writes_and_recovers_capacity(
    tmp_path: Path,
    server_factory: Callable[..., ServerHandle],
) -> None:
    started = time.monotonic()
    server = server_factory(
        trusted_ingress_env={
            "PLAN_TRUSTED_INGRESS_PROVIDER": "tailscale",
            "PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN": "owner@example.test",
            "PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN": "https://panels.example.test",
        }
    )
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.test",
            "commit",
            "-qm",
            "fixture",
            "--allow-empty",
        ],
        check=True,
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PANELS_REAL_"))
    }
    edge = {"Host": "panels.example.test", "Tailscale-User-Login": "owner@example.test"}
    records: list[dict[str, Any]] = []
    with ExitStack() as stack, httpx.Client(base_url=server.base, timeout=10) as owner:
        project = _json(
            owner,
            "POST",
            "/api/projects",
            json={"name": "Preview saturation", "folder_path": str(repo)},
        )
        for number in range(2):
            ticket = _json(
                owner,
                "POST",
                "/api/tickets",
                json={
                    "title": f"Saturation {number}",
                    "worker_type": "coding",
                    "brief": "Synthetic",
                    "project_id": project["id"],
                    "manager": {"kind": "chief", "id": "chief"},
                },
            )
            worktree = repo / "worktrees" / f"{ticket['id']}-preview"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree)],
                check=True,
                capture_output=True,
            )
            worker = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket["id"]}
            prepared = _json(owner, "POST", "/api/previews/prepare", headers=worker)
            process = subprocess.Popen(
                [sys.executable, "-c", APP],
                cwd=worktree,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )

            def stop(process: subprocess.Popen[bytes] = process) -> None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if process.stderr:
                    process.stderr.close()

            stack.callback(stop)
            deadline = time.monotonic() + 5
            while not (worktree / "port").exists():
                assert process.poll() is None, process.stderr.read() if process.stderr else ""
                assert time.monotonic() < deadline
                time.sleep(0.01)
            registered = _json(
                owner,
                "POST",
                "/api/previews/register",
                headers=worker,
                json={"port": int((worktree / "port").read_text())},
            )
            assert prepared["generation"] == registered["generation"]
            records.append(registered)

        async def exercise() -> None:
            async with httpx.AsyncClient(
                base_url=server.base, timeout=10, limits=httpx.Limits(max_connections=100)
            ) as client:

                async def ordinary_work(phase: str) -> None:
                    for _ in range(10):
                        begin = time.monotonic()
                        response = await client.get("/api/tickets?detail=summary")
                        assert response.status_code == 200
                        assert time.monotonic() - begin < 1, phase
                    begin = time.monotonic()
                    response = await client.patch(
                        f"/api/tickets/{records[0]['ticket_id']}", json={"title": phase}
                    )
                    assert response.status_code == 200, response.text
                    assert time.monotonic() - begin < 1, phase

                # Vite uses Content-Length. Panels compression must replace it
                # without truncating or double-decoding the proxied application.
                sized = await client.get(records[0]["base_path"] + "sized", headers=edge)
                assert sized.status_code == 200
                assert sized.headers["content-encoding"] == "gzip"
                assert sized.content == b"x" * 8192

                for phase in ["slow", "fast"]:
                    responses: list[httpx.Response] = []
                    try:
                        for record in records:
                            for _ in range(16):
                                request = client.build_request(
                                    "GET", record["base_path"] + phase, headers=edge
                                )
                                response = await client.send(request, stream=True)
                                assert response.status_code == 200, await response.aread()
                                responses.append(response)
                        begin = time.monotonic()
                        excess = await client.get(records[0]["base_path"] + phase, headers=edge)
                        assert excess.status_code == 503, excess.text
                        assert time.monotonic() - begin < 1
                        await ordinary_work(phase)
                    finally:
                        await asyncio.gather(*(response.aclose() for response in responses))
                    # Disconnected silent streams must release before their 30-second idle timeout.
                    deadline = time.monotonic() + 2
                    while True:
                        response = await client.send(
                            client.build_request(
                                "GET", records[0]["base_path"] + "slow", headers=edge
                            ),
                            stream=True,
                        )
                        status = response.status_code
                        await response.aclose()
                        if status == 200:
                            break
                        assert status == 503
                        assert time.monotonic() < deadline
                        await asyncio.sleep(0.02)

        asyncio.run(exercise())
    print(f"PREVIEW_SATURATION_SECONDS={time.monotonic() - started:.3f}")


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
