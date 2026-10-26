"""Synthetic Mac process: production executor and a loopback-only web fixture.

Both synthetic hosts run on one Linux machine. No native child is started and no
provider, physical Mac, SSH host, or deployed route is contacted.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import ssl
from pathlib import Path

import aiohttp

from planner.executor.configuration import ExecutorPaths
from planner.executor.mac import MacExecutor


async def serve(root: Path, origin: str, ca_path: Path, port: int) -> None:
    async def http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            path = request.split(b" ", 2)[1]
            if path == b"/asset.js":
                body = b"document.querySelector('#asset').textContent = 'Mac asset rendered';"
                content_type = b"text/javascript"
            else:
                body = (
                    b"<!doctype html><html><head><title>Synthetic Mac preview</title></head>"
                    b"<body><h1>Synthetic Mac preview</h1><p id='asset'>Loading asset</p>"
                    b"<script src='asset.js'></script></body></html>"
                )
                content_type = b"text/html"
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: "
                + content_type
                + b"\r\nContent-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def session_factory() -> aiohttp.ClientSession:
        return aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=ssl.create_default_context(cafile=str(ca_path))),
            cookie_jar=aiohttp.DummyCookieJar(),
            trust_env=False,
            auto_decompress=False,
        )

    mac = MacExecutor(
        paths=ExecutorPaths(root),
        revision="a" * 40,
        executor_id="synthetic-preview",
        panels_server_url=origin,
        session_factory=session_factory,
    )
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def stop_all() -> None:
        mac._stop.set()
        stop.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop_all)
    # Cut the executor while leaving the web server alive: a VPS-loopback fallback
    # would then incorrectly succeed, so the browser's 503 proves transport selection.
    loop.add_signal_handler(signal.SIGUSR1, mac._stop.set)
    server = await asyncio.start_server(http, "127.0.0.1", port)
    async with server:
        task = asyncio.create_task(mac.run())
        try:
            await stop.wait()
        finally:
            mac._stop.set()
            await task


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--ca", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.root, arguments.origin, arguments.ca, arguments.port))
