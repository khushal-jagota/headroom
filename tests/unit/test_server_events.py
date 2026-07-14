from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection
from typing import Any

from fastapi.testclient import TestClient

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import EventKind
from planner.core.db import connect, create_schema
from planner.core.events import append_event
from planner.core.server import create_app
from planner.core.ws import tail_events


class RecordingWebSocket:
    def __init__(self) -> None:
        self.accepted = asyncio.Event()
        self.sent: list[dict[str, Any]] = []
        self._received: asyncio.Queue[dict[str, str]] = asyncio.Queue()

    async def accept(self) -> None:
        self.accepted.set()

    async def receive(self) -> dict[str, str]:
        return await self._received.get()

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    def disconnect(self) -> None:
        self._received.put_nowait({"type": "websocket.disconnect"})


async def _wait_for_sent_count(websocket: RecordingWebSocket, count: int) -> None:
    deadline = asyncio.get_running_loop().time() + 1.0
    while len(websocket.sent) < count:
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError(f"timed out waiting for {count} frames: {websocket.sent!r}")
        await asyncio.sleep(0.001)


def _db_path(tmp_path: Path) -> Path:
    db_path = tmp_path / "planning-test.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    return db_path


def _conn_factory(db_path: Path) -> Connection:
    return connect(str(db_path))


def _make_app(tmp_path: Path) -> Any:
    db_path = _db_path(tmp_path)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_WS_HEARTBEAT_MS": "125",
        },
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), build_adapters(config), conn_factory)


def test_meta_serves_event_stream_heartbeat_cadence(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/meta")

    assert response.status_code == 200
    assert response.json()["ws_heartbeat_ms"] == 125


def test_tail_events_sends_quiet_heartbeat_without_advancing_cursor(tmp_path: Path) -> None:
    async def run() -> None:
        db_path = _db_path(tmp_path)
        websocket = RecordingWebSocket()
        task = asyncio.create_task(
            tail_events(
                websocket,
                since=7,
                conn_factory=lambda: _conn_factory(db_path),
                ws_poll_ms=100,
                events_read_limit=100,
                ws_heartbeat_ms=5,
            )
        )
        await websocket.accepted.wait()
        await _wait_for_sent_count(websocket, 1)
        websocket.disconnect()
        await asyncio.wait_for(task, timeout=0.2)
        assert websocket.sent == [{"events": [], "cursor": 7}]

    asyncio.run(run())


def test_tail_events_preserves_event_batches_and_heartbeats_at_current_cursor(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        db_path = _db_path(tmp_path)
        conn = connect(str(db_path))
        try:
            event_id = append_event(
                conn,
                "t_ws_batch",
                EventKind.ticket_updated,
                {"field": "title", "from": "Old", "to": "New"},
                created_at=11,
            )
            conn.commit()
        finally:
            conn.close()

        websocket = RecordingWebSocket()
        task = asyncio.create_task(
            tail_events(
                websocket,
                since=0,
                conn_factory=lambda: _conn_factory(db_path),
                ws_poll_ms=100,
                events_read_limit=100,
                ws_heartbeat_ms=5,
            )
        )
        await websocket.accepted.wait()
        await _wait_for_sent_count(websocket, 2)
        websocket.disconnect()
        await asyncio.wait_for(task, timeout=0.2)

        assert websocket.sent[0] == {
            "events": [
                {
                    "id": event_id,
                    "entity_id": "t_ws_batch",
                    "kind": "ticket_updated",
                    "payload": {"field": "title", "from": "Old", "to": "New"},
                    "created_at": 11,
                }
            ],
            "cursor": event_id,
        }
        assert websocket.sent[1] == {"events": [], "cursor": event_id}

    asyncio.run(run())


def test_tail_events_disconnect_exits_promptly_during_quiet_period(tmp_path: Path) -> None:
    async def run() -> None:
        db_path = _db_path(tmp_path)
        websocket = RecordingWebSocket()
        task = asyncio.create_task(
            tail_events(
                websocket,
                since=0,
                conn_factory=lambda: _conn_factory(db_path),
                ws_poll_ms=5000,
                events_read_limit=100,
                ws_heartbeat_ms=5000,
            )
        )
        await websocket.accepted.wait()
        websocket.disconnect()
        await asyncio.wait_for(task, timeout=0.2)

    asyncio.run(run())
