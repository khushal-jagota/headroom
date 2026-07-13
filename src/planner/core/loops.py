"""Production lifecycle for employee execution and optional readiness polling."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from time import monotonic as _monotonic
from typing import Any

from planner.chat import data as chat_data
from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.db import connect
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
        shutdown_grace_seconds: float = 30.0,
    ) -> None:
        self._tasks = tasks
        self.employee_step_runner = employee_step_runner
        self.ticket_readiness_loop = ticket_readiness_loop
        self.readiness_doorbell = readiness_doorbell or NoOpReadinessDoorbell()
        self._lock_path = lock_path
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._stopped = False

    async def stop(self, *, deadline: float | None = None) -> None:
        """Stop discovery, drain accepted employee work, then release resources."""
        global _active
        if self._stopped:
            return
        self._stopped = True
        if deadline is None:
            deadline = _monotonic() + self._shutdown_grace_seconds
        if self.ticket_readiness_loop is not None:
            await asyncio.to_thread(_stop_with_deadline, self.ticket_readiness_loop, deadline)
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
            "SELECT id FROM tickets WHERE ticket_status = 'agent_running_step' ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        ticket_id = str(row["id"])
        try:
            employee_step_runner.recover_running_step(ticket_id)
        except Exception:
            _LOGGER.exception("failed to admit restart recovery for Ticket %s", ticket_id)


def _settle_stale_worker_turns_after_ticket_handoff(config: Config, clock: Clock) -> None:
    conn = connect(config.db_path, config.db_busy_timeout_ms)
    try:
        if not _has_tables(conn, ("tickets", "chat_turns")):
            return
        rows = conn.execute(
            "SELECT chat_turns.id, chat_turns.entity_id, chat_turns.output_role "
            "FROM chat_turns "
            "JOIN tickets ON tickets.id = chat_turns.entity_id "
            "WHERE chat_turns.status = 'running' "
            "AND chat_turns.origin = 'worker' "
            "AND chat_turns.mode = 'worker_step' "
            "AND tickets.ticket_status <> 'agent_running_step' "
            "ORDER BY chat_turns.started_at, chat_turns.id"
        ).fetchall()
        now = clock.now_unix()
        for row in rows:
            chat_data.finish_turn(
                conn,
                str(row["id"]),
                entity_id=str(row["entity_id"]),
                reply_text="",
                output_role=str(row["output_role"]),
                status="interrupted",
                now=now,
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
        _settle_stale_worker_turns_after_ticket_handoff(config, clock)
        _recover_running_ticket_steps(config, employee_step_runner)
    elif not ensure_machine_lock(config.dispatcher_lock_path):
        _LOGGER.info(
            "Ticket readiness loop not started: another process holds the polling lock"
        )
        employee_step_runner = build_runner(readiness_doorbell)
        _settle_stale_worker_turns_after_ticket_handoff(config, clock)
        _recover_running_ticket_steps(config, employee_step_runner)
    else:
        candidate_runner: EmployeeStepRunner | None = None
        candidate_loop: TicketReadinessLoop | None = None
        loop_slot: list[TicketReadinessLoop] = []

        def wake_loop() -> None:
            loop_slot[0].wake()

        candidate_doorbell = LoopReadinessDoorbell(wake_loop)
        try:
            candidate_runner = build_runner(candidate_doorbell)
            _settle_stale_worker_turns_after_ticket_handoff(config, clock)
            _recover_running_ticket_steps(config, candidate_runner)
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
        shutdown_grace_seconds=float(config.shutdown_grace_seconds),
    )
    _active = loops
    return loops
