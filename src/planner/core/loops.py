"""Production background loops: the boundary tick every tick_seconds via
asyncio.to_thread, and — replacing the removed dispatcher loop — System A, the readiness
poll that drives System B. An iteration's exception is logged and never kills the loop.
Test mode never starts these; that gating lives in the lifespan (core/server.py), so this
module stays directly callable for tests.

System A runs on its own daemon thread (its fast-path poke is a threading.Event, not an
asyncio sleep to interrupt). It is guarded by the master switch (config.dispatch_enabled —
W3a delegated: System A reuses it) and the relocated machine-wide lock
(runtime.lock.ensure_machine_lock), so only one poller drives the board per machine. System
B is constructed with the planner home + Hermes interpreter from config; its real gateway
spawn is only ever reachable here (outside test mode), so ./verify stays hermetic."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from collections.abc import Callable
from functools import partial

from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.days.scheduler import run_boundary_tick
from planner.minds.config import resolve_hermes_python, resolve_planner_home
from planner.minds.gateway import spawn_popen
from planner.runtime.lock import ensure_machine_lock, release_machine_lock
from planner.runtime.system_a import SystemA
from planner.runtime.system_b import SystemB

_LOGGER = logging.getLogger(__name__)

ConnFactory = Callable[[], sqlite3.Connection]


async def _loop_forever(name: str, tick: Callable[[], JsonDict], interval: int) -> None:
    """One background loop: tick first, then sleep. A4: shield the in-flight tick thread so
    cancellation waits it out. Catch Exception only so CancelledError propagates."""
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
    def __init__(
        self,
        tasks: list[asyncio.Task[None]],
        system_a: SystemA | None = None,
        lock_path: str | None = None,
    ) -> None:
        self._tasks = tasks
        self.system_a = system_a  # read by the lifespan -> app.state.system_a (API poke seam)
        self._lock_path = lock_path
        self._stopped = False

    async def stop(self) -> None:
        """Stop System A (join its poll thread — no new set-offs after this), release the
        machine lock, then cancel the boundary task and wait out any in-flight tick thread.
        Idempotent: a re-stopped handle is a no-op."""
        global _active
        if self._stopped:
            return
        self._stopped = True
        if self.system_a is not None:
            await asyncio.to_thread(self.system_a.stop)
        if self._lock_path is not None:
            release_machine_lock(self._lock_path)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if _active is self:
            _active = None


_active: BackgroundLoops | None = None


def _start_system_a(config: Config, clock: Clock) -> SystemA | None:
    """Construct + start System A when enabled and this process wins the machine lock. Any
    construction failure logs, releases the lock, and degrades to boundary-only (never kills
    the server)."""
    if not config.dispatch_enabled:
        _LOGGER.info("System A disabled (dispatch_enabled=false)")
        return None
    if not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info("System A not started: another process holds the machine lock")
        return None
    try:
        system_b = SystemB(
            config.db_path,
            clock,
            home=resolve_planner_home(),
            hermes_python=resolve_hermes_python(),
            spawn=spawn_popen,
        )
        system_a = SystemA(
            config.db_path, clock, system_b,
            role=config.worker_skill, boundary_hour=config.boundary_hour,
        )
        system_b.set_idle_callback(system_a.poke)  # finished step -> poll next step (fast path)
        system_a.start(config.tick_seconds)
        return system_a
    except Exception:
        _LOGGER.exception("System A failed to start; running boundary-only")
        release_machine_lock(config.dispatcher_lock_path)
        return None


def start_background_loops(
    config: Config, clock: Clock, adapters: Adapters, conn_factory: ConnFactory
) -> BackgroundLoops:
    """Start the boundary loop + (when enabled) System A. At most one live instance per
    process."""
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")
    boundary = asyncio.create_task(
        _loop_forever(
            "boundary",
            partial(run_boundary_tick, conn_factory, config, clock, adapters),
            config.tick_seconds,
        )
    )
    system_a = _start_system_a(config, clock)
    lock_path = config.dispatcher_lock_path if system_a is not None else None
    loops = BackgroundLoops([boundary], system_a, lock_path)
    _active = loops
    return loops
