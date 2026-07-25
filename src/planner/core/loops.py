"""Production lifecycle for Employee execution and optional automatic discovery."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from time import monotonic as _monotonic
from typing import Any

from planner.core import change_signal
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.db import connect
from planner.runtime.automatic_employee_step_discovery_loop import (
    AutomaticEmployeeStepDiscoveryLoop,
)
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
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
        stop_waking_discovery_on_change: Callable[[], None] | None = None,
        shutdown_grace_seconds: float = 30.0,
    ) -> None:
        self._tasks = tasks
        self.employee_step_runner = employee_step_runner
        self.automatic_employee_step_discovery_loop = automatic_employee_step_discovery_loop
        self._stop_waking_discovery_on_change = stop_waking_discovery_on_change
        self._lock_path = lock_path
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._stopped = False

    async def stop(self, *, deadline: float | None = None) -> None:
        """Stop discovery, drain accepted employee work, then release resources."""
        global _active
        if self._stopped:
            return
        self._stopped = True
        if self._stop_waking_discovery_on_change is not None:
            self._stop_waking_discovery_on_change()
            self._stop_waking_discovery_on_change = None
        if deadline is None:
            deadline = _monotonic() + self._shutdown_grace_seconds
        if self.automatic_employee_step_discovery_loop is not None:
            await asyncio.to_thread(
                _stop_with_deadline,
                self.automatic_employee_step_discovery_loop,
                deadline,
            )
        await asyncio.to_thread(_stop_with_deadline, self.employee_step_runner, deadline)
        if self._lock_path is not None:
            release_machine_lock(self._lock_path)
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if _active is self:
            _active = None


_active: BackgroundLoops | None = None


def _stop_with_deadline(target: Any, deadline: float) -> None:
    target.stop(deadline=deadline)


def _recover_running_ticket_steps(
    config: Config, employee_step_runner: EmployeeStepRunner
) -> None:
    conn = connect(config.db_path, config.db_busy_timeout_ms)
    try:
        if not _has_tables(conn, ("tickets",)):
            return
        rows = conn.execute(
            "SELECT id FROM tickets WHERE ticket_status = 'agent' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        ticket_id = str(row["id"])
        try:
            employee_step_runner.recover_running_step(ticket_id)
        except Exception:
            _LOGGER.exception("failed to admit restart recovery for Ticket %s", ticket_id)


def _settle_stale_employee_steps_after_ticket_handoff(config: Config, clock: Clock) -> None:
    conn = connect(config.db_path, config.db_busy_timeout_ms)
    try:
        if not _has_tables(conn, ("tickets", "employee_step_runs")):
            return
        SqliteEmployeeStepRepository().interrupt_all_stale_handoffs(
            conn,
            now=clock.now_unix(),
        )
    finally:
        conn.close()


def _has_tables(conn: sqlite3.Connection, table_names: tuple[str, ...]) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        f"AND name IN ({','.join('?' for _ in table_names)})",
        table_names,
    ).fetchall()
    return {str(row["name"]) for row in rows} == set(table_names)


def start_background_loops(
    config: Config,
    clock: Clock,
    *,
    step_gateway: Any,
) -> BackgroundLoops:
    """Always compose Employee execution; optionally own automatic discovery.

    Production supplies the one ACP ``step_gateway``. While the discovery loop runs it
    is subscribed to the change signal, so any committed write asks it to look again
    instead of waiting out its periodic timer. Over-waking costs a read-only
    re-check of the complete eligibility decision."""
    global _active
    if _active is not None:
        raise RuntimeError("background loops already running")

    def build_runner() -> EmployeeStepRunner:
        return EmployeeStepRunner(
            config.db_path,
            clock,
            gateway=step_gateway,
            boundary_hour=config.boundary_hour,
            busy_timeout_ms=config.db_busy_timeout_ms,
        )

    employee_step_runner: EmployeeStepRunner
    automatic_employee_step_discovery_loop: AutomaticEmployeeStepDiscoveryLoop | None = None
    lock_path: str | None = None
    stop_waking_discovery_on_change: Callable[[], None] | None = None

    if not config.dispatch_enabled:
        _LOGGER.info("Automatic Employee-step discovery disabled (dispatch_enabled=false)")
        employee_step_runner = build_runner()
        _settle_stale_employee_steps_after_ticket_handoff(config, clock)
        _recover_running_ticket_steps(config, employee_step_runner)
    elif not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info(
            "Automatic Employee-step discovery not started: another process holds the polling lock"
        )
        employee_step_runner = build_runner()
        _settle_stale_employee_steps_after_ticket_handoff(config, clock)
        _recover_running_ticket_steps(config, employee_step_runner)
    else:
        candidate_runner: EmployeeStepRunner | None = None
        candidate_loop: AutomaticEmployeeStepDiscoveryLoop | None = None
        candidate_unsubscribe: Callable[[], None] | None = None
        try:
            candidate_runner = build_runner()
            _settle_stale_employee_steps_after_ticket_handoff(config, clock)
            _recover_running_ticket_steps(config, candidate_runner)
            candidate_loop = AutomaticEmployeeStepDiscoveryLoop(
                config.db_path,
                clock,
                candidate_runner,
                boundary_hour=config.boundary_hour,
                busy_timeout_ms=config.db_busy_timeout_ms,
            )
            candidate_loop.start(config.tick_seconds)
            candidate_unsubscribe = change_signal.subscribe(candidate_loop.wake)
        except Exception:
            _LOGGER.exception(
                "Automatic Employee-step discovery failed to start; "
                "direct employee revisions remain available"
            )
            if candidate_unsubscribe is not None:
                candidate_unsubscribe()
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
            employee_step_runner = build_runner()
        else:
            assert candidate_runner is not None
            assert candidate_loop is not None
            employee_step_runner = candidate_runner
            automatic_employee_step_discovery_loop = candidate_loop
            stop_waking_discovery_on_change = candidate_unsubscribe
            lock_path = config.dispatcher_lock_path

    loops = BackgroundLoops(
        [],
        employee_step_runner,
        automatic_employee_step_discovery_loop,
        lock_path,
        stop_waking_discovery_on_change,
        shutdown_grace_seconds=float(config.shutdown_grace_seconds),
    )
    _active = loops
    return loops
