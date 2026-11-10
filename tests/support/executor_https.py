"""Isolated TLS ingress with synthetic trusted identity, never real Serve proof."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import uvicorn
from aiohttp import web
from fastapi import FastAPI


@contextlib.contextmanager
def short_transport_root() -> Iterator[Path]:
    """Keep Unix sockets below macOS's path limit, regardless of test name."""
    with tempfile.TemporaryDirectory(
        prefix="ex-", dir=Path(tempfile.gettempdir()).resolve()
    ) as root:
        yield Path(root)


@dataclass(frozen=True)
class HttpsProxy:
    origin: str
    ca_path: Path
    control: str | None = None

    def cut_connections(self) -> None:
        """Abort every open connection through the proxy, as a dropped link does.

        Requests in flight get no reply. Later requests connect again normally."""
        self._control("cut")

    def blackhole(self, enabled: bool = True, *, drop_held: bool = True) -> None:
        """While on, the proxy stops reading and forwarding, as when the network silently
        drops packets: every socket stays open, and no request in flight or new gets a
        reply. Turning it off lets later requests pass. With drop_held the requests it
        held are aborted; without it they stay dead, open and unanswered, until the
        client gives up on its own timeout (a long poll on a socket that died in sleep)."""
        if enabled:
            self._control("blackhole/on")
        else:
            self._control("blackhole/off" if drop_held else "blackhole/off-keep-dead")

    def _control(self, action: str) -> None:
        if self.control is None:
            raise RuntimeError("this proxy has no control listener")
        request = urllib.request.Request(f"{self.control}/{action}", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()

    def session_factory(self) -> aiohttp.ClientSession:
        context = ssl.create_default_context(cafile=str(self.ca_path))
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=context),
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            auto_decompress=False,
        )


@contextlib.contextmanager
def isolated_https_proxy(
    root: Path,
    upstream: str,
    login: str = "owner@example.com",
) -> Iterator[HttpsProxy]:
    """Start only an owned test proxy. Its certificate is trusted only in test clients."""
    root.mkdir(parents=True, exist_ok=True)
    cert, key, ready = root / "cert.pem", root / "key.pem", root / "ready.json"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    ready.unlink(missing_ok=True)
    with (root / "proxy.log").open("wb") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.support.executor_https",
                "--upstream",
                upstream,
                "--cert",
                str(cert),
                "--key",
                str(key),
                "--ready",
                str(ready),
                "--login",
                login,
                "--parent",
                str(os.getpid()),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError((root / "proxy.log").read_text())
            time.sleep(0.01)
        published = json.loads(ready.read_text())
        yield HttpsProxy(published["origin"], cert, published["control"])
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@contextlib.asynccontextmanager
async def serve_executor_app(app: FastAPI, root: Path) -> AsyncIterator[HttpsProxy]:
    """Reuse the caller's app and endpoint with a real isolated HTTP/WS host."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            log_level="error",
            lifespan="off",
            ws_max_size=1024 * 1024 + 4,
            ws_max_queue=1,
            ws_per_message_deflate=False,
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("isolated app exited before startup")
                await asyncio.sleep(0.01)
        upstream = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with isolated_https_proxy(root, upstream) as proxy:
            yield proxy
    finally:
        server.should_exit = True
        try:
            done, _ = await asyncio.wait((task,), timeout=5)
            if not done:
                # A request still held (a long poll) must not hold the shutdown.
                server.force_exit = True
                await asyncio.wait_for(task, 5)
        finally:
            listener.close()


_HOP_HEADERS = {
    "connection",
    "upgrade",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "host",
    "sec-websocket-key",
    "sec-websocket-version",
    "sec-websocket-extensions",
    "sec-websocket-protocol",
}


async def _serve(upstream: str, cert: Path, key: Path, ready: Path, login: str) -> None:
    session = aiohttp.ClientSession(
        cookie_jar=aiohttp.DummyCookieJar(),
        trust_env=False,
        auto_decompress=False,
    )

    # Each blackhole period has a number. A request that was in flight or arrived during
    # one is dead: it is never answered, and its socket stays open until the period is
    # dropped, the client gives up, or the request is very old.
    blackhole = {"on": False, "period": 0}
    drops: dict[int, asyncio.Event] = {}
    runner: web.AppRunner | None = None

    def cut() -> None:
        server = runner.server if runner is not None else None
        for handler in tuple(server.connections if server is not None else ()):
            transport = handler.transport
            if transport is not None:
                transport.abort()

    class Dead(Exception):
        pass

    class Life:
        """When this request began, and so which blackhole period kills it."""

        def __init__(self) -> None:
            self.period = blackhole["period"]
            self.began_dark = blackhole["on"]

        def killer(self) -> int | None:
            if self.began_dark:
                return self.period
            if blackhole["period"] != self.period:
                return self.period + 1
            return None

        async def check(self, request: web.Request) -> None:
            period = self.killer()
            if period is None:
                return
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(drops[period].wait(), 900)
            if request.transport is not None:
                request.transport.abort()
            raise Dead

    async def body(request: web.Request, life: Life) -> AsyncIterator[bytes]:
        async for chunk in request.content.iter_chunked(256 * 1024):
            await life.check(request)
            yield chunk

    async def control(request: web.Request) -> web.Response:
        action = request.match_info["action"]
        if action == "cut":
            cut()
        elif action == "blackhole/on":
            if not blackhole["on"]:
                blackhole["period"] += 1
                drops[blackhole["period"]] = asyncio.Event()
                blackhole["on"] = True
        elif action in ("blackhole/off", "blackhole/off-keep-dead"):
            if blackhole["on"]:
                blackhole["on"] = False
                if action == "blackhole/off":
                    drops[blackhole["period"]].set()
        else:
            return web.Response(status=404)
        return web.Response(text="ok")

    async def proxy(request: web.Request) -> web.StreamResponse:
        life = Life()
        try:
            return await forward(request, life)
        except Dead:
            return web.Response(status=502)  # never reaches the aborted socket

    async def forward(request: web.Request, life: Life) -> web.StreamResponse:
        await life.check(request)
        connection_headers = {
            name.strip().lower() for name in request.headers.get("connection", "").split(",")
        }
        headers = [
            (name.decode("latin1"), value.decode("latin1"))
            for name, value in request.raw_headers
            if name.decode("latin1").lower() not in _HOP_HEADERS | connection_headers
            and not name.decode("latin1").lower().startswith("tailscale-")
        ]
        if login:
            headers.append(("Tailscale-User-Login", login))
        url = upstream + request.raw_path
        if request.headers.get("upgrade", "").lower() == "websocket":
            max_message = (
                64 * 1024
                if request.path.startswith("/api/executor/previews/")
                else (1024 * 1024 + 4)
            )
            try:
                remote_ws = await session.ws_connect(
                    url,
                    headers=headers,
                    compress=0,
                    max_msg_size=max_message + 1,
                    autoclose=True,
                )
            except aiohttp.WSServerHandshakeError as error:
                return web.Response(status=error.status, text="upstream websocket refused")
            async with remote_ws:
                local = web.WebSocketResponse(compress=False, max_msg_size=max_message + 1)
                await local.prepare(request)

                async def copy(source, target) -> None:  # type: ignore[no-untyped-def]
                    async for message in source:
                        if message.type == aiohttp.WSMsgType.BINARY:
                            await target.send_bytes(message.data)
                        elif message.type == aiohttp.WSMsgType.TEXT:
                            await target.send_str(message.data)
                        elif message.type == aiohttp.WSMsgType.ERROR:
                            raise ConnectionError("proxy websocket failed")

                tasks = (
                    asyncio.create_task(copy(local, remote_ws)),
                    asyncio.create_task(copy(remote_ws, local)),
                )
                try:
                    await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await local.close()
                return local
        async with session.request(
            request.method,
            url,
            headers=headers,
            data=body(request, life) if request.can_read_body else None,
            allow_redirects=False,
        ) as remote:
            await life.check(request)
            response = web.StreamResponse(
                status=remote.status,
                headers=[
                    (name, value)
                    for name, value in remote.headers.items()
                    if name.lower() not in _HOP_HEADERS
                ],
            )
            await response.prepare(request)
            async for chunk in remote.content.iter_chunked(256 * 1024):
                await life.check(request)
                await response.write(chunk)
            await life.check(request)
            await response.write_eof()
            return response

    app = web.Application(client_max_size=0)
    app.router.add_route("*", "/{path:.*}", proxy)
    runner = web.AppRunner(app, access_log=None)
    control_app = web.Application()
    control_app.router.add_post("/{action:.*}", control)
    control_runner = web.AppRunner(control_app, access_log=None)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    try:
        await runner.setup()
        await control_runner.setup()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        site = web.SockSite(runner, listener, ssl_context=context)
        await site.start()
        control_listener = socket.socket()
        control_listener.bind(("127.0.0.1", 0))
        control_listener.listen()
        await web.SockSite(control_runner, control_listener).start()
        # Publish readiness only after the complete JSON file has been closed.
        temporary_ready = ready.with_suffix(".tmp")
        temporary_ready.write_text(
            json.dumps(
                {
                    "origin": f"https://127.0.0.1:{listener.getsockname()[1]}",
                    "control": f"http://127.0.0.1:{control_listener.getsockname()[1]}",
                }
            )
        )
        temporary_ready.replace(ready)
        await asyncio.Event().wait()
    finally:
        await control_runner.cleanup()
        await runner.cleanup()
        await session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--login", required=True)
    parser.add_argument("--parent", type=int, required=True)
    args = parser.parse_args()
    if sys.platform == "linux":
        import ctypes

        # The kernel ends this proxy with the process that started it.
        ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG
    if os.getppid() != args.parent:
        raise SystemExit(1)
    asyncio.run(_serve(args.upstream, args.cert, args.key, args.ready, args.login))
