"""The neutral downstream WebSocket route (sibling of `relay_route.py`, plan §0, §6).

Accept -> `register_downstream` -> swap in the bounded `OverflowSignallingQueue` (so the
REAL backpressure queue is bounded) -> construct a `NeutralDownstreamSession` -> run its
writer + receive loops -> `unregister_downstream`. Same auth posture and teardown
discipline as `relay_route.py`; it translates instead of sending verbatim.

Overload policy = disconnect-the-slow-consumer: when the bounded queue overflows (the browser
is not draining), the route closes the connection; on reconnect the pane re-attaches and
re-syncs history from the durable session (the S1 death-recovery model).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from planner.hermes_backend.employee_child_relay import EmployeeChildRelay
from planner.hermes_backend.neutral_downstream_session import NeutralDownstreamSession
from planner.hermes_backend.neutral_relay_config import NEUTRAL_OUTBOUND_MAX_FRAMES

# How long the writer parks waiting for the next outbound frame before re-checking the
# overflow flag, so a fully-stuck socket still notices overload even with nothing to send.
_OVERFLOW_WAKE_SECONDS = 0.1


class OverflowSignallingQueue(asyncio.Queue[str]):
    """A bounded `asyncio.Queue` that records overflow instead of losing it silently.

    The relay fan-out wraps `put_nowait` in `except Exception: pass` (S1
    `_safe_put`/`_enqueue_text`), so a raised `QueueFull` would be swallowed and the frame
    dropped with no signal. This subclass catches its own `QueueFull`, sets `overflowed`,
    and drops the frame — making overload OBSERVABLE so the route can disconnect the slow
    consumer instead of silently corrupting the pane's turn state."""

    def __init__(self, maxsize: int = NEUTRAL_OUTBOUND_MAX_FRAMES) -> None:
        super().__init__(maxsize=maxsize)
        self.overflowed = False

    def put_nowait(self, item: str) -> None:
        try:
            super().put_nowait(item)
        except asyncio.QueueFull:
            self.overflowed = True


async def relay_neutral_downstream_websocket(
    websocket: WebSocket,
    *,
    pool_provider: Callable[[], Any | None],
    relay: EmployeeChildRelay | None,
    db_path: str,
    unavailable_close_code: int = 1013,
) -> None:
    if pool_provider() is None or relay is None:
        await websocket.accept()
        await websocket.close(code=unavailable_close_code, reason="relay backend unavailable")
        return

    await websocket.accept()
    conn = relay.register_downstream()
    # Bound the REAL backpressure queue BEFORE any frame can flow (the relay reads
    # `conn.outbound` fresh on every enqueue/drain, so swapping the attribute is safe and
    # needs no relay change — plan §6).
    conn.outbound = OverflowSignallingQueue()
    bounded = conn.outbound

    async def send_neutral(text: str) -> None:
        await websocket.send_text(text)

    session = NeutralDownstreamSession(
        relay=relay,
        conn=conn,
        send_neutral=send_neutral,
        db_path=db_path,
        pool_provider=pool_provider,
    )
    # The writer loop below is the sole drainer of `conn.outbound`; request-time RPC
    # correlation awaits futures instead of self-draining (avoids two queue consumers).
    session.enable_background_pump()

    async def writer_loop() -> None:
        while True:
            if bounded.overflowed:
                return
            text = await conn.outbound.get()
            await session.handle_outbound_text(text)

    async def receive_loop() -> None:
        while True:
            raw = await websocket.receive_text()
            # A cut/unknown request kind is rejected inside the session (neutral error, no
            # native frame) — the route never needs to parse or branch on the kind.
            await session.handle_neutral_wire_text(raw)

    async def overflow_monitor() -> None:
        # INDEPENDENT observer (defect #2): the writer can be parked forever inside
        # websocket.send_text, so it cannot notice overflow between dequeues. This task polls
        # the flag on its own and completes the moment overload is signalled — driving the
        # route's teardown (close + unregister) even while the writer is blocked.
        while not bounded.overflowed:
            await asyncio.sleep(_OVERFLOW_WAKE_SECONDS)

    writer_task = asyncio.ensure_future(writer_loop())
    receive_task = asyncio.ensure_future(receive_loop())
    monitor_task = asyncio.ensure_future(overflow_monitor())
    tasks = (writer_task, receive_task, monitor_task)
    try:
        await asyncio.wait(set(tasks), return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if bounded.overflowed:
            with contextlib.suppress(Exception):
                await websocket.close(code=1013, reason="downstream overloaded")
        relay.unregister_downstream(conn)
