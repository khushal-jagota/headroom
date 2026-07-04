"""§9 event tailer. WS /api/events?since=<id> tails the events table and pushes
{events, cursor} batches — an invalidation signal, non-empty batches only, no
server-side reconciliation. One sqlite connection per WS connection, created and
used on the event-loop thread (sqlite3 same-thread) and closed when the coroutine
ends, including on cancellation.

A per-connection watcher task drains the receive side: it awaits websocket.receive()
until the ASGI "websocket.disconnect" arrives — sent both when the client closes and
when uvicorn tears the connection down on shutdown. The poll loop wakes on that signal
(instead of sleeping blind), so a parked idle tailer exits promptly on Ctrl-C and never
holds graceful shutdown open, and a silently-gone client is noticed without a send.

FastAPI appears here because this is part of the server shell (the server.py family)."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable

from fastapi import WebSocket, WebSocketDisconnect

from planner.core.contracts import EventRow, JsonDict
from planner.core.events import read_events_since


def _event_json(row: EventRow) -> JsonDict:
    return {
        "id": row.id,
        "entity_id": row.entity_id,
        "kind": row.kind,
        "payload": row.payload,
        "created_at": row.created_at,
    }


async def _watch_disconnect(websocket: WebSocket) -> None:
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


async def tail_events(
    websocket: WebSocket,
    since: int,
    conn_factory: Callable[[], sqlite3.Connection],
    ws_poll_ms: int,
    events_read_limit: int,
) -> None:
    await websocket.accept()
    conn = conn_factory()
    watcher = asyncio.create_task(_watch_disconnect(websocket))
    try:
        cursor = since
        while not watcher.done():
            batch = read_events_since(conn, cursor, events_read_limit)
            if batch:
                cursor = batch[-1].id
                await websocket.send_json(
                    {"events": [_event_json(e) for e in batch], "cursor": cursor}
                )
                if len(batch) == events_read_limit:
                    continue
            else:
                await asyncio.wait({watcher}, timeout=ws_poll_ms / 1000)
    except WebSocketDisconnect:
        pass
    finally:
        watcher.cancel()
        try:
            await asyncio.wait({watcher})
        finally:
            conn.close()
