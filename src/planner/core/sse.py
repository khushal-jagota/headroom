"""The browser's live-update stream, served at GET /api/changes.

The stream carries one unnamed ``change`` frame per committed write and nothing else —
no entity, no kind, no payload. The browser answers a frame by refetching whatever it is
currently showing, so there is no vocabulary here to keep in step with the backend.
Frames coalesce: writes that land while a frame is already owed produce one frame, not a
queue of them.

When nothing has changed the stream sends a comment line instead. A comment is invisible
to the browser's EventSource, keeps anything in between from calling the connection dead,
and — because a gone client makes the write fail — is also how a stream notices it has no
reader left.

This module also holds the set of streams that are currently open, because a server being
shut down has to close them itself: they are idle by nature, and waiting for their clients
to leave first would mean waiting forever.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import AsyncIterator, Callable

from planner.core import change_signal

_log = logging.getLogger(__name__)

CHANGE_FRAME = "data: change\n\n"
HEARTBEAT_FRAME = ": keep-alive\n\n"

_lock = threading.Lock()
_open_stream_closers: list[Callable[[], None]] = []


class _ChangeStreamState:
    """What one stream owes its client. Only ever touched on the event-loop thread."""

    def __init__(self) -> None:
        self.changed = False
        self.closing = False


def close_open_change_streams() -> None:
    """Ask every open stream to finish.

    Called from the process's signal handler, so every closer hands the actual work to
    its own event loop rather than doing it here.
    """
    with _lock:
        closers = tuple(_open_stream_closers)
    for close in closers:
        try:
            close()
        except Exception:
            _log.exception("change stream close request failed")


def open_change_stream_count() -> int:
    with _lock:
        return len(_open_stream_closers)


async def change_stream(heartbeat_ms: int) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    state = _ChangeStreamState()
    woken = asyncio.Event()

    def mark_changed() -> None:
        state.changed = True
        woken.set()

    def mark_closing() -> None:
        state.closing = True
        woken.set()

    def on_change() -> None:
        loop.call_soon_threadsafe(mark_changed)

    def close() -> None:
        loop.call_soon_threadsafe(mark_closing)

    heartbeat_seconds = heartbeat_ms / 1000
    unsubscribe = change_signal.subscribe(on_change)
    with _lock:
        _open_stream_closers.append(close)
    try:
        while True:
            try:
                await asyncio.wait_for(woken.wait(), timeout=heartbeat_seconds)
            except TimeoutError:
                pass
            woken.clear()
            if state.closing:
                return
            if state.changed:
                state.changed = False
                yield CHANGE_FRAME
            else:
                yield HEARTBEAT_FRAME
    finally:
        with _lock:
            if close in _open_stream_closers:
                _open_stream_closers.remove(close)
        unsubscribe()
