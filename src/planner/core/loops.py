"""Production background loops: two asyncio tasks driving the dispatcher tick and
the boundary tick every tick_seconds via asyncio.to_thread. An iteration's exception
is logged and never kills the loop. Test mode never starts these — that gating lives
in T09's lifespan; this module stays directly callable for tests."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from functools import partial

from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.days.scheduler import run_boundary_tick
from planner.dispatch.runtime import ConnFactory, release_dispatcher_lock, run_tick

_LOGGER = logging.getLogger(__name__)


async def _loop_forever(name: str, tick: Callable[[], JsonDict], interval: int) -> None:
    """One background loop: tick first (the first dispatcher iteration performs the
    once-per-process lock acquisition), then sleep. A4: shield the in-flight tick
    thread so cancellation waits it out — the lock is never released under a running
    tick. Catch Exception only so CancelledError propagates."""
    while True:
        thread_task = asyncio.ensure_future(asyncio.to_thread(tick))
        try:
            await asyncio.shield(thread_task)
        except asyncio.CancelledError:
            with contextlib.suppress(BaseException):
                await thread_task
            raise
        except Exception:
            _LOGGER.exception("background %s tick failed", name)
        await asyncio.sleep(interval)


class BackgroundLoops:
    def __init__(self, tasks: list[asyncio.Task[None]], lock_path: str) -> None:
        self._tasks = tasks
        self._lock_path = lock_path
        self._stopped = False

    async def stop(self) -> None:
        """Cancel both tasks and wait them out (which also waits out any in-flight
        tick thread), then release the dispatcher lock. Idempotent: a re-stopped
        handle is a no-op and can never release a newer instance's lock (A2)."""
        global _active
        if self._stopped:
            return
        self._stopped = True
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if _active is self:
            release_dispatcher_lock(self._lock_path)
            _active = None


_active: BackgroundLoops | None = None


def start_background_loops(
    config: Config, clock: Clock, adapters: Adapters, conn_factory: ConnFactory
) -> BackgroundLoops:
    """Start the two loops on the running event loop. A2: at most one live instance
    per process (they share the cached flock fd)."""
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")
    dispatcher = asyncio.create_task(
        _loop_forever(
            "dispatcher",
            partial(run_tick, conn_factory, config, clock, adapters),
            config.tick_seconds,
        )
    )
    boundary = asyncio.create_task(
        _loop_forever(
            "boundary",
            partial(run_boundary_tick, conn_factory, config, clock, adapters),
            config.tick_seconds,
        )
    )
    loops = BackgroundLoops([dispatcher, boundary], config.dispatcher_lock_path)
    _active = loops
    return loops
