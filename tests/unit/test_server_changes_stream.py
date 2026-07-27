"""GET /api/changes: what the browser is told, and when the stream lets go.

The endpoint is driven straight as an ASGI application, because the FastAPI test client
collects a whole response before it hands one back and this response never ends on its
own.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from time import monotonic
from typing import Any

import pytest
from fastapi import FastAPI

from planner.core import change_signal, sse
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _make_app(tmp_path: Path, *, sse_heartbeat_ms: int) -> FastAPI:
    db_path = tmp_path / "changes.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_SSE_HEARTBEAT_MS": str(sse_heartbeat_ms),
        },
    )

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), conn_factory)


def _scope() -> dict[str, object]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/changes",
        "raw_path": b"/api/changes",
        "root_path": "",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 51234),
        "server": ("127.0.0.1", 8767),
    }


async def _await_open_stream() -> None:
    deadline = monotonic() + 5
    while sse.open_change_stream_count() == 0:
        assert monotonic() < deadline, "the change stream never opened"
        await asyncio.sleep(0.005)


async def _collect_frames(
    app: FastAPI,
    *,
    frames_wanted: int,
    once_open: Callable[[], Awaitable[None]] | None = None,
) -> tuple[dict[str, object], list[str]]:
    """Serve /api/changes until `frames_wanted` frames have been sent."""
    started: dict[str, object] = {}
    frames: list[str] = []
    enough = asyncio.Event()
    disconnected = asyncio.Event()

    async def receive() -> dict[str, object]:
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: MutableMapping[str, Any]) -> None:
        if message["type"] == "http.response.start":
            started.update(message)
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            assert isinstance(body, bytes)
            if body:
                frames.append(body.decode("utf-8"))
                if len(frames) >= frames_wanted:
                    enough.set()

    serving = asyncio.create_task(app(_scope(), receive, send))
    try:
        await _await_open_stream()
        if once_open is not None:
            await once_open()
        await asyncio.wait_for(enough.wait(), timeout=5)
    finally:
        disconnected.set()
        await asyncio.wait_for(serving, timeout=5)
    return started, frames


async def _await_closed_stream() -> None:
    """The generator's clean-up runs when its consumer is finalized, not before."""
    deadline = monotonic() + 5
    while sse.open_change_stream_count() != 0:
        assert monotonic() < deadline, "the change stream never closed"
        await asyncio.sleep(0.005)


def test_the_stream_is_an_event_stream_that_says_only_that_something_changed(
    tmp_path: Path,
) -> None:
    app = _make_app(tmp_path, sse_heartbeat_ms=60_000)

    async def emit_once() -> None:
        change_signal.emit()

    started, frames = asyncio.run(
        _collect_frames(app, frames_wanted=1, once_open=emit_once)
    )

    assert started["status"] == 200
    headers = dict(started["headers"])  # type: ignore[call-overload]
    assert headers[b"content-type"] == b"text/event-stream; charset=utf-8"
    assert frames == ["data: change\n\n"]


def test_a_signal_raised_on_another_thread_reaches_the_stream(tmp_path: Path) -> None:
    """Writers commit on their own threads; the stream lives on the event loop."""
    app = _make_app(tmp_path, sse_heartbeat_ms=60_000)

    async def emit_from_a_worker_thread() -> None:
        emitting = threading.Thread(target=change_signal.emit)
        emitting.start()
        emitting.join(5)
        assert not emitting.is_alive()

    _started, frames = asyncio.run(
        _collect_frames(app, frames_wanted=1, once_open=emit_from_a_worker_thread)
    )

    assert frames == ["data: change\n\n"]


def test_signals_raised_while_a_frame_is_owed_coalesce_into_one_frame(
    tmp_path: Path,
) -> None:
    app = _make_app(tmp_path, sse_heartbeat_ms=60_000)

    async def emit_five_then_one_more() -> None:
        for _ in range(5):
            change_signal.emit()
        # Let the five land as one frame before asking for the next.
        await asyncio.sleep(0.05)
        change_signal.emit()

    _started, frames = asyncio.run(
        _collect_frames(app, frames_wanted=2, once_open=emit_five_then_one_more)
    )

    assert frames == ["data: change\n\n", "data: change\n\n"]


def test_a_quiet_stream_sends_comment_lines_at_the_configured_cadence(
    tmp_path: Path,
) -> None:
    app = _make_app(tmp_path, sse_heartbeat_ms=50)

    started_at = monotonic()
    _started, frames = asyncio.run(_collect_frames(app, frames_wanted=3))
    elapsed = monotonic() - started_at

    assert frames == [": keep-alive\n\n"] * 3
    # Three heartbeats at 50ms cannot have arrived faster than two intervals.
    assert elapsed >= 0.1


def test_a_client_that_leaves_takes_its_subscription_with_it(tmp_path: Path) -> None:
    app = _make_app(tmp_path, sse_heartbeat_ms=50)
    subscribers_before = change_signal.subscriber_count()

    async def serve_then_watch() -> int:
        await _collect_frames(app, frames_wanted=1)
        await _await_closed_stream()
        return change_signal.subscriber_count()

    assert asyncio.run(serve_then_watch()) == subscribers_before


def test_closing_the_open_streams_ends_them_without_waiting_for_their_clients(
    tmp_path: Path,
) -> None:
    app = _make_app(tmp_path, sse_heartbeat_ms=60_000)

    async def serve() -> list[str]:
        frames: list[str] = []
        never_disconnects = asyncio.Event()

        async def receive() -> dict[str, object]:
            await never_disconnects.wait()
            return {"type": "http.disconnect"}

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                assert isinstance(body, bytes)
                if body:
                    frames.append(body.decode("utf-8"))

        serving = asyncio.create_task(app(_scope(), receive, send))
        await _await_open_stream()

        # This is exactly what the server's SIGTERM handler does, off the event loop.
        closing = threading.Thread(target=sse.close_open_change_streams)
        closing.start()
        closing.join(5)

        # The client is still there and no frame was ever owed; the response ends anyway.
        await asyncio.wait_for(serving, timeout=5)
        await _await_closed_stream()
        return frames

    assert asyncio.run(serve()) == []


@pytest.mark.parametrize("heartbeat_ms", [1, 25])
def test_the_heartbeat_cadence_comes_from_configuration(
    tmp_path: Path, heartbeat_ms: int
) -> None:
    app = _make_app(tmp_path, sse_heartbeat_ms=heartbeat_ms)

    _started, frames = asyncio.run(_collect_frames(app, frames_wanted=2))

    assert frames == [": keep-alive\n\n"] * 2
