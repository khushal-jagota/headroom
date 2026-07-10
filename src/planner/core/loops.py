"""Production lifecycle for employee execution and optional readiness polling."""

from __future__ import annotations

import asyncio
import logging

from planner.core.clock import Clock
from planner.core.config import Config
from planner.minds.shared_gateway import SharedGateway
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.lock import ensure_machine_lock, release_machine_lock
from planner.runtime.readiness_doorbell import (
    LoopReadinessDoorbell,
    NoOpReadinessDoorbell,
    ReadinessDoorbell,
)
from planner.runtime.ticket_readiness_loop import TicketReadinessLoop

_LOGGER = logging.getLogger(__name__)


class BackgroundLoops:
    def __init__(
        self,
        tasks: list[asyncio.Task[None]],
        employee_step_runner: EmployeeStepRunner,
        ticket_readiness_loop: TicketReadinessLoop | None = None,
        lock_path: str | None = None,
        readiness_doorbell: ReadinessDoorbell | None = None,
    ) -> None:
        self._tasks = tasks
        self.employee_step_runner = employee_step_runner
        self.ticket_readiness_loop = ticket_readiness_loop
        self.readiness_doorbell = readiness_doorbell or NoOpReadinessDoorbell()
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

    def build_runner(doorbell: ReadinessDoorbell) -> EmployeeStepRunner:
        return EmployeeStepRunner(
            config.db_path,
            clock,
            gateway=shared_gateway,
            readiness_doorbell=doorbell,
            boundary_hour=config.boundary_hour,
            busy_timeout_ms=config.db_busy_timeout_ms,
        )

    readiness_doorbell: ReadinessDoorbell = NoOpReadinessDoorbell()
    employee_step_runner: EmployeeStepRunner
    ticket_readiness_loop: TicketReadinessLoop | None = None
    lock_path: str | None = None

    if not config.dispatch_enabled:
        _LOGGER.info("Ticket readiness loop disabled (dispatch_enabled=false)")
        employee_step_runner = build_runner(readiness_doorbell)
    elif not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info(
            "Ticket readiness loop not started: another process holds the polling lock"
        )
        employee_step_runner = build_runner(readiness_doorbell)
    else:
        candidate_runner: EmployeeStepRunner | None = None
        candidate_loop: TicketReadinessLoop | None = None
        loop_slot: list[TicketReadinessLoop] = []

        def wake_loop() -> None:
            loop_slot[0].wake()

        candidate_doorbell = LoopReadinessDoorbell(wake_loop)
        try:
            candidate_runner = build_runner(candidate_doorbell)
            candidate_loop = TicketReadinessLoop(
                config.db_path,
                clock,
                candidate_runner,
                boundary_hour=config.boundary_hour,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            loop_slot.append(candidate_loop)
            candidate_loop.start(config.tick_seconds)
        except Exception:
            _LOGGER.exception(
                "Ticket readiness loop failed to start; direct employee revisions remain available"
            )
            if candidate_loop is not None:
                try:
                    candidate_loop.stop()
                except Exception:
                    _LOGGER.exception("partially started Ticket readiness loop failed to stop")
            if candidate_runner is not None:
                try:
                    candidate_runner.stop()
                except Exception:
                    _LOGGER.exception("discarded employee runner failed to stop")
            release_machine_lock(config.dispatcher_lock_path)
            readiness_doorbell = NoOpReadinessDoorbell()
            employee_step_runner = build_runner(readiness_doorbell)
        else:
            assert candidate_runner is not None
            assert candidate_loop is not None
            readiness_doorbell = candidate_doorbell
            employee_step_runner = candidate_runner
            ticket_readiness_loop = candidate_loop
            lock_path = config.dispatcher_lock_path

    loops = BackgroundLoops(
        [],
        employee_step_runner,
        ticket_readiness_loop,
        lock_path,
        readiness_doorbell,
    )
    _active = loops
    return loops
