"""The one downstream WebSocket route. Same auth posture as `/api/events`
(middleware-only). Mirrors `tail_events`'s structure (accept, writer loop, receive
loop, cleanup) and terminates + unregisters when EITHER the receive side or the writer
side ends, tracking in-flight `_forward` tasks so teardown drains them (plan §1 R-3a/R-10).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from planner.hermes_backend.employee_child_relay import EmployeeChildRelay


async def relay_downstream_websocket(
    websocket: WebSocket,
    *,
    pool_provider: Callable[[], Any | None],
    relay: EmployeeChildRelay | None,
    unavailable_close_code: int = 1013,
) -> None:
    if pool_provider() is None or relay is None:
        # Backend off/unavailable: accept-then-close 1013 (matches tail_events posture).
        await websocket.accept()
        await websocket.close(code=unavailable_close_code, reason="relay backend unavailable")
        return

    await websocket.accept()
    conn = relay.register_downstream()
    pending_forwards: set[asyncio.Task[None]] = set()

    async def writer_loop() -> None:
        while True:
            text = await conn.outbound.get()
            await websocket.send_text(text)

    async def receive_loop() -> None:
        while True:
            raw = await websocket.receive_text()
            forward = relay.handle_downstream_message(conn, raw)
            if forward is not None:
                task = asyncio.ensure_future(forward)
                pending_forwards.add(task)
                task.add_done_callback(pending_forwards.discard)

    writer_task = asyncio.ensure_future(writer_loop())
    receive_task = asyncio.ensure_future(receive_loop())
    try:
        await asyncio.wait({writer_task, receive_task}, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in (writer_task, receive_task):
            task.cancel()
        for task in list(pending_forwards):
            task.cancel()
        to_drain = [writer_task, receive_task, *pending_forwards]
        if to_drain:
            await asyncio.gather(*to_drain, return_exceptions=True)
        relay.unregister_downstream(conn)
