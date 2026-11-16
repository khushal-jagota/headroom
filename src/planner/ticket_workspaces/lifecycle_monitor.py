"""Wake cleanup when the authoritative ended-Ticket set changes."""

import asyncio
import logging
import sqlite3
from collections.abc import Awaitable, Callable
from contextlib import closing

from planner.core import change_signal
from planner.core.db import connect

_log = logging.getLogger(__name__)
type LifecycleSnapshot = tuple[tuple[str, int, int], ...]


def ended_ticket_snapshot(db_path: str) -> LifecycleSnapshot:
    with closing(connect(db_path)) as conn:
        return tuple(
            (str(row[0]), int(row[1]), int(row[2]))
            for row in conn.execute(
                "SELECT ticket_id,ended_at,deleted FROM ticket_workspace_lifecycle "
                "WHERE ended_at IS NOT NULL ORDER BY ticket_id"
            )
        )


async def monitor_lifecycle(
    read: Callable[[], LifecycleSnapshot], cleanup: Callable[[], Awaitable[None]], interval: float
) -> None:
    loop = asyncio.get_running_loop()
    event = asyncio.Event()

    def wake() -> None:
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:
            pass  # Loop shutdown may race an in-flight commit.

    unsubscribe = change_signal.subscribe(wake)
    previous: LifecycleSnapshot | None = None
    retry_at = loop.time()
    try:
        while True:
            try:
                snapshot = await asyncio.to_thread(read)
                if loop.time() >= retry_at or previous != snapshot:
                    retry_at = loop.time() + interval
                    await cleanup()
                previous = snapshot
            except (OSError, sqlite3.Error):
                _log.exception("Ticket lifecycle check failed")
                retry_at = loop.time() + interval
            except Exception:
                _log.exception("Ticket workspace cleanup failed")
                retry_at = loop.time() + interval
            try:
                await asyncio.wait_for(event.wait(), timeout=max(0, retry_at - loop.time()))
                event.clear()
            except TimeoutError:
                pass
    finally:
        unsubscribe()
