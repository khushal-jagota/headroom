"""Production background runtime for System A, the readiness poll that drives
System B. Test mode never starts it; that gating lives in the lifespan
(core/server.py), so this module stays directly callable for tests.

System A runs on its own daemon thread (its fast-path poke is a threading.Event, not an
asyncio sleep to interrupt). It is guarded by the startup switch (config.dispatch_enabled)
and the relocated machine-wide lock
(runtime.lock.ensure_machine_lock), so only one poller drives the board per machine. System
B is constructed with the planner home + Hermes interpreter from config; its real gateway
spawn is only ever reachable here (outside test mode), so ./verify stays hermetic."""

from __future__ import annotations

import asyncio
import logging

from planner.core.clock import Clock
from planner.core.config import Config
from planner.minds.shared_gateway import SharedGateway
from planner.runtime.lock import ensure_machine_lock, release_machine_lock
from planner.runtime.system_a import SystemA
from planner.runtime.system_b import SystemB

_LOGGER = logging.getLogger(__name__)


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
        machine lock, then cancel any background tasks. Idempotent: a re-stopped handle is
        a no-op."""
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


def _start_system_a(config: Config, clock: Clock, gateway: SharedGateway) -> SystemA | None:
    """Construct + start System A when startup-enabled and this process wins the machine lock.
    Any construction failure logs, releases the lock, and leaves the server running without
    the worker poller."""
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
            gateway=gateway,
        )
        system_a = SystemA(
            config.db_path, clock, system_b,
            role=config.worker_skill, boundary_hour=config.boundary_hour,
        )
        system_b.set_idle_callback(system_a.poke)  # finished step -> poll next step (fast path)
        system_a.start(config.tick_seconds)
        return system_a
    except Exception:
        _LOGGER.exception("System A failed to start; worker poller disabled")
        release_machine_lock(config.dispatcher_lock_path)
        return None


def start_background_loops(
    config: Config,
    clock: Clock,
    *,
    shared_gateway: SharedGateway,
) -> BackgroundLoops:
    """Start System A when enabled. At most one live instance per process."""
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")
    system_a = _start_system_a(config, clock, shared_gateway)
    lock_path = config.dispatcher_lock_path if system_a is not None else None
    loops = BackgroundLoops([], system_a, lock_path)
    _active = loops
    return loops
