"""Production lifecycle for employee execution and optional readiness polling."""

from __future__ import annotations

import asyncio
import logging

from planner.core.clock import Clock
from planner.core.config import Config
from planner.minds.shared_gateway import SharedGateway
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.lock import ensure_machine_lock, release_machine_lock
from planner.runtime.ticket_readiness_loop import TicketReadinessLoop

_LOGGER = logging.getLogger(__name__)


class BackgroundLoops:
    def __init__(
        self,
        tasks: list[asyncio.Task[None]],
        employee_step_runner: EmployeeStepRunner,
        ticket_readiness_loop: TicketReadinessLoop | None = None,
        lock_path: str | None = None,
    ) -> None:
        self._tasks = tasks
        self.employee_step_runner = employee_step_runner
        self.ticket_readiness_loop = ticket_readiness_loop
        self._lock_path = lock_path
        self._stopped = False

    async def stop(self) -> None:
        """Stop discovery, drain accepted employee work, then release resources."""
        global _active
        if self._stopped:
            return
        self._stopped = True
        if self.ticket_readiness_loop is not None:
            await asyncio.to_thread(self.ticket_readiness_loop.stop)
        await asyncio.to_thread(self.employee_step_runner.stop)
        if self._lock_path is not None:
            release_machine_lock(self._lock_path)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if _active is self:
            _active = None


_active: BackgroundLoops | None = None


def start_background_loops(
    config: Config,
    clock: Clock,
    *,
    shared_gateway: SharedGateway,
) -> BackgroundLoops:
    """Always compose employee execution; optionally own readiness discovery."""
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")

    employee_step_runner = EmployeeStepRunner(
        config.db_path,
        clock,
        gateway=shared_gateway,
        boundary_hour=config.boundary_hour,
        busy_timeout_ms=config.db_busy_timeout_ms,
    )
    ticket_readiness_loop: TicketReadinessLoop | None = None
    lock_path: str | None = None

    if not config.dispatch_enabled:
        _LOGGER.info("Ticket readiness loop disabled (dispatch_enabled=false)")
    elif not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info(
            "Ticket readiness loop not started: another process holds the polling lock"
        )
    else:
        try:
            ticket_readiness_loop = TicketReadinessLoop(
                config.db_path,
                clock,
                employee_step_runner,
                boundary_hour=config.boundary_hour,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            employee_step_runner.set_idle_callback(ticket_readiness_loop.poke)
            ticket_readiness_loop.start(config.tick_seconds)
            lock_path = config.dispatcher_lock_path
        except Exception:
            _LOGGER.exception(
                "Ticket readiness loop failed to start; direct employee revisions remain available"
            )
            release_machine_lock(config.dispatcher_lock_path)
            ticket_readiness_loop = None

    loops = BackgroundLoops(
        [],
        employee_step_runner,
        ticket_readiness_loop,
        lock_path,
    )
    _active = loops
    return loops
