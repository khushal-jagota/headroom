"""Production lifecycle for Employee execution and optional automatic discovery."""

from __future__ import annotations

import asyncio
import logging

from planner.core.clock import Clock
from planner.core.config import Config
from planner.minds.shared_gateway import SharedGateway
from planner.runtime.automatic_employee_step_discovery_loop import (
    AutomaticEmployeeStepDiscoveryLoop,
)
from planner.runtime.automatic_employee_step_eligibility_wake import (
    AutomaticEmployeeStepEligibilityWake,
    LoopAutomaticEmployeeStepEligibilityWake,
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.lock import ensure_machine_lock, release_machine_lock

_LOGGER = logging.getLogger(__name__)


class BackgroundLoops:
    def __init__(
        self,
        tasks: list[asyncio.Task[None]],
        employee_step_runner: EmployeeStepRunner,
        automatic_employee_step_discovery_loop: AutomaticEmployeeStepDiscoveryLoop | None = None,
        lock_path: str | None = None,
        automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake
        | None = None,
    ) -> None:
        self._tasks = tasks
        self.employee_step_runner = employee_step_runner
        self.automatic_employee_step_discovery_loop = automatic_employee_step_discovery_loop
        self.automatic_employee_step_eligibility_wake = (
            automatic_employee_step_eligibility_wake or NoOpAutomaticEmployeeStepEligibilityWake()
        )
        self._lock_path = lock_path
        self._stopped = False

    async def stop(self) -> None:
        """Stop discovery, drain accepted employee work, then release resources."""
        global _active
        if self._stopped:
            return
        self._stopped = True
        if self.automatic_employee_step_discovery_loop is not None:
            await asyncio.to_thread(self.automatic_employee_step_discovery_loop.stop)
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
    """Always compose Employee execution; optionally own automatic discovery."""
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")

    def build_runner(
        eligibility_wake: AutomaticEmployeeStepEligibilityWake,
    ) -> EmployeeStepRunner:
        return EmployeeStepRunner(
            config.db_path,
            clock,
            gateway=shared_gateway,
            automatic_employee_step_eligibility_wake=eligibility_wake,
            boundary_hour=config.boundary_hour,
            busy_timeout_ms=config.db_busy_timeout_ms,
        )

    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake = (
        NoOpAutomaticEmployeeStepEligibilityWake()
    )
    employee_step_runner: EmployeeStepRunner
    automatic_employee_step_discovery_loop: AutomaticEmployeeStepDiscoveryLoop | None = None
    lock_path: str | None = None

    if not config.dispatch_enabled:
        _LOGGER.info("Automatic Employee-step discovery disabled (dispatch_enabled=false)")
        employee_step_runner = build_runner(automatic_employee_step_eligibility_wake)
    elif not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info(
            "Automatic Employee-step discovery not started: another process holds the polling lock"
        )
        employee_step_runner = build_runner(automatic_employee_step_eligibility_wake)
    else:
        candidate_runner: EmployeeStepRunner | None = None
        candidate_loop: AutomaticEmployeeStepDiscoveryLoop | None = None
        loop_slot: list[AutomaticEmployeeStepDiscoveryLoop] = []

        def wake_loop() -> None:
            loop_slot[0].wake()

        candidate_eligibility_wake = LoopAutomaticEmployeeStepEligibilityWake(wake_loop)
        try:
            candidate_runner = build_runner(candidate_eligibility_wake)
            candidate_loop = AutomaticEmployeeStepDiscoveryLoop(
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
                "Automatic Employee-step discovery failed to start; "
                "direct employee revisions remain available"
            )
            if candidate_loop is not None:
                try:
                    candidate_loop.stop()
                except Exception:
                    _LOGGER.exception(
                        "partially started Automatic Employee-step discovery loop failed to stop"
                    )
            if candidate_runner is not None:
                try:
                    candidate_runner.stop()
                except Exception:
                    _LOGGER.exception("discarded employee runner failed to stop")
            release_machine_lock(config.dispatcher_lock_path)
            automatic_employee_step_eligibility_wake = NoOpAutomaticEmployeeStepEligibilityWake()
            employee_step_runner = build_runner(automatic_employee_step_eligibility_wake)
        else:
            assert candidate_runner is not None
            assert candidate_loop is not None
            automatic_employee_step_eligibility_wake = candidate_eligibility_wake
            employee_step_runner = candidate_runner
            automatic_employee_step_discovery_loop = candidate_loop
            lock_path = config.dispatcher_lock_path

    loops = BackgroundLoops(
        [],
        employee_step_runner,
        automatic_employee_step_discovery_loop,
        lock_path,
        automatic_employee_step_eligibility_wake,
    )
    _active = loops
    return loops
