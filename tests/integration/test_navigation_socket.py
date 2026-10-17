"""Real TCP delivery boundaries on synthetic transport-only SQLite state.

No ownership/startup claim is exercised here: the fixture publishes an already
prepared epoch so these checks isolate socket backpressure and read consistency.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sqlite3
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import uvicorn
from fastapi import FastAPI

from planner.core.sse import open_change_stream_count
from planner.navigation.configuration import DeliveryConfig
from planner.navigation.contracts import Continuity, ProjectRecord, RevisionBatch
from planner.navigation.delivery import DeliveryRuntime
from planner.navigation.delivery_contracts import compact_json


def prepare(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            "CREATE TABLE navigation_meta(singleton,scope,schema_version,epoch,revision);"
            "INSERT INTO navigation_meta VALUES(1,'socket-fixture',2,'epoch',0);"
            "CREATE TABLE navigation_records(kind,id,value);"
            "CREATE TABLE navigation_dirty(kind,id);"
            "CREATE TABLE navigation_journal(revision INTEGER PRIMARY KEY,body TEXT);"
            "CREATE TABLE navigation_delivery_retention(singleton INTEGER PRIMARY KEY,"
            "epoch TEXT,floor_revision INTEGER);"
        )


def append(path: Path, revision: int, title: str) -> None:
    continuity: Continuity = {
        "scope": "socket-fixture",
        "schema_version": 2,
        "semantics": "generic-source-facts-v2",
        "epoch": "epoch",
        "revision": str(revision),
    }
    record: ProjectRecord = {
        "kind": "project",
        "id": "p",
        "value": {"name": title, "priority": None},
    }
    batch: RevisionBatch = {
        **continuity,
        "base_revision": str(revision - 1),
        "upserts": [record],
        "deletes": [],
    }
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM navigation_records")
        conn.execute(
            "INSERT INTO navigation_records VALUES('project','p',?)",
            (compact_json(record["value"]),),
        )
        conn.execute("INSERT INTO navigation_journal VALUES(?,?)", (revision, compact_json(batch)))
        conn.execute("UPDATE navigation_meta SET revision=?", (revision,))


async def eventually(predicate: Callable[[], bool], timeout: float = 3) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@contextlib.asynccontextmanager
async def serving(
    path: Path, *, send_timeout: float = 0.2
) -> AsyncIterator[tuple[str, DeliveryRuntime]]:
    config = replace(DeliveryConfig(), poll_seconds=0.01, send_timeout_seconds=send_timeout)
    runtime = DeliveryRuntime(path, config)
    runtime.ready = True
    app = FastAPI()
    app.include_router(runtime.router)
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, log_level="critical", access_log=False, lifespan="off", timeout_graceful_shutdown=2
        )
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        await eventually(lambda: server.started)
        yield f"http://127.0.0.1:{port}", runtime
    finally:
        # Same ordering as production: publication streams drain before runtime owner release.
        await asyncio.wait_for(runtime.close(), 3)
        server.should_exit = True
        await asyncio.wait_for(task, 3)
        listener.close()


class Feed:
    def __init__(self, initial: dict[str, Any]) -> None:
        self.records = {(r["kind"], r["id"]): r for r in initial["records"]}
        self.revision = int(initial["revision"])
        self.cursor = ""
        self.error: BaseException | None = None

    async def consume(self, client: httpx.AsyncClient, cursor: str) -> None:
        try:
            async with client.stream(
                "GET", "/api/navigation/events", headers={"Last-Event-ID": cursor}
            ) as response:
                response.raise_for_status()
                pending = b""
                async for chunk in response.aiter_raw():
                    pending += chunk
                    while b"\n\n" in pending:
                        frame, pending = pending.split(b"\n\n", 1)
                        if b"event: batch" not in frame:
                            assert b"event: reset" not in frame
                            continue
                        lines = frame.splitlines()
                        body = json.loads(
                            next(line[6:] for line in lines if line.startswith(b"data: "))
                        )
                        assert int(body["base_revision"]) == self.revision
                        for record in body["upserts"]:
                            self.records[(record["kind"], record["id"])] = record
                        for key in body["deletes"]:
                            self.records.pop((key["kind"], key["id"]), None)
                        self.revision = int(body["revision"])
                        self.cursor = next(
                            line[4:].decode() for line in lines if line.startswith(b"id: ")
                        )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self.error = exc
            raise

    async def reached(self, revision: int) -> None:
        def ready() -> bool:
            if self.error:
                raise self.error
            return self.revision >= revision

        await eventually(ready)


async def cancel(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_tcp_snapshot_live_stream_reconnect_disconnect_and_shutdown(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "navigation.db"
        prepare(path)
        baseline = open_change_stream_count()
        async with serving(path) as (url, runtime):
            async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=5) as client:
                response = await client.get("/api/navigation/snapshot")
                response.raise_for_status()
                initial = response.json()
                feed = Feed(initial["snapshot"])
                task = asyncio.create_task(feed.consume(client, initial["cursor"]))
                try:
                    await eventually(lambda: open_change_stream_count() == baseline + 1)
                    await asyncio.to_thread(append, path, 1, "continuous")
                    await feed.reached(1)
                    await cancel(task)
                    await eventually(lambda: open_change_stream_count() == baseline)
                    # Genuine disconnected history must arrive after reconnecting with last SSE id.
                    await asyncio.to_thread(append, path, 2, "disconnected")
                    task = asyncio.create_task(feed.consume(client, feed.cursor))
                    await feed.reached(2)
                    final = (await client.get("/api/navigation/snapshot")).json()["snapshot"]
                    assert feed.revision == int(final["revision"])
                    assert (
                        sorted(feed.records.values(), key=lambda r: (r["kind"], r["id"]))
                        == final["records"]
                    )
                    # An idle real TCP response must terminate from publication shutdown itself.
                    await asyncio.wait_for(runtime.close(), 1)
                    await asyncio.wait_for(task, 1)
                    assert open_change_stream_count() == baseline
                finally:
                    await cancel(task)
        assert open_change_stream_count() == baseline

    asyncio.run(run())


def test_tcp_blocked_reader_deadline_does_not_block_fast_client(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "navigation.db"
        prepare(path)
        baseline = open_change_stream_count()
        async with serving(path) as (url, runtime):
            async with httpx.AsyncClient(base_url=url, trust_env=False, timeout=5) as client:
                initial = (await client.get("/api/navigation/snapshot")).json()
                feed = Feed(initial["snapshot"])
                fast = asyncio.create_task(feed.consume(client, initial["cursor"]))
                slow = socket.socket()
                slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
                slow.setblocking(False)
                loop = asyncio.get_running_loop()
                try:
                    port = int(url.rsplit(":", 1)[1])
                    await loop.sock_connect(slow, ("127.0.0.1", port))
                    request = (
                        f"GET /api/navigation/events?cursor={quote(initial['cursor'])} HTTP/1.1\r\n"
                        f"Host: 127.0.0.1:{port}\r\n\r\n"
                    )
                    await loop.sock_sendall(slow, request.encode())
                    headers = b""
                    async with asyncio.timeout(1):
                        while b"\r\n\r\n" not in headers:
                            headers += await loop.sock_recv(slow, 1024)
                    assert b"200 OK" in headers
                    await eventually(lambda: open_change_stream_count() == baseline + 2)
                    # Never read this TCP socket again. Tiny receive/send windows plus 1.2MB
                    # of valid moderate frames force uvicorn's write flow control to wait.
                    for revision in range(1, 13):
                        await asyncio.to_thread(
                            append, path, revision, str(revision) + "x" * 100_000
                        )
                    await feed.reached(12)
                    await eventually(lambda: open_change_stream_count() == baseline + 1, timeout=2)
                    assert not fast.done(), "fast subscriber was disconnected with slow subscriber"
                    await asyncio.to_thread(append, path, 13, "fast still receives")
                    await feed.reached(13)
                    final = (await client.get("/api/navigation/snapshot")).json()["snapshot"]
                    assert list(feed.records.values()) == final["records"]
                    assert runtime.ready
                finally:
                    slow.close()
                    await cancel(fast)
                    await eventually(lambda: open_change_stream_count() == baseline)
        assert open_change_stream_count() == baseline

    asyncio.run(run())
