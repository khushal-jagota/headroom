"""Isolated TLS ingress with synthetic trusted identity, never real Serve proof."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import time
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
    with tempfile.TemporaryDirectory(prefix="ex-", dir=Path("/tmp").resolve()) as root:
        yield Path(root)


@dataclass(frozen=True)
class HttpsProxy:
    origin: str
    ca_path: Path

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
        yield HttpsProxy(json.loads(ready.read_text())["origin"], cert)
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

    async def proxy(request: web.Request) -> web.StreamResponse:
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
            data=request.content.iter_chunked(256 * 1024) if request.can_read_body else None,
            allow_redirects=False,
        ) as remote:
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
                await response.write(chunk)
            await response.write_eof()
            return response

    app = web.Application(client_max_size=0)
    app.router.add_route("*", "/{path:.*}", proxy)
    runner = web.AppRunner(app, access_log=None)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    try:
        await runner.setup()
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        site = web.SockSite(runner, listener, ssl_context=context)
        await site.start()
        # Publish readiness only after the complete JSON file has been closed.
        temporary_ready = ready.with_suffix(".tmp")
        temporary_ready.write_text(
            json.dumps({"origin": f"https://127.0.0.1:{listener.getsockname()[1]}"})
        )
        temporary_ready.replace(ready)
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--cert", type=Path, required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--login", required=True)
    args = parser.parse_args()
    asyncio.run(_serve(args.upstream, args.cert, args.key, args.ready, args.login))
