"""Discover automatically runnable Tickets on today's board."""

from __future__ import annotations

import logging
import threading

from planner.core.clock import Clock
from planner.core.db import connect
from planner.days.logic import dates
from planner.runtime import readiness
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket

_log = logging.getLogger(__name__)

_CANDIDATE_SQL = (
    "SELECT t.id FROM tickets t "
    "JOIN day_tickets dt ON dt.ticket_id = t.id "
    "WHERE dt.day_id = ? "
    "AND t.ticket_status = 'empty' AND t.stage NOT IN ('done','dropped')"
)


class TicketReadinessLoop:
    """Read-only discovery plus a wakeable timer backstop."""

    def __init__(
        self,
        db_path: str,
        clock: Clock,
        employee_step_runner: EmployeeStepRunner,
        *,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._employee_step_runner = employee_step_runner
        self._boundary_hour = boundary_hour
        self._busy_timeout_ms = busy_timeout_ms
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def wake(self) -> None:
        """Wake readiness discovery now; the timer remains a backstop."""
        self._wake.set()

    def poll_once(self) -> list[str]:
        """Discover ready Tickets and pass only their ids to the employee runner."""
        today_id = dates.resolve_day_id("today", self._clock.now(), self._boundary_hour)
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            rows = conn.execute(_CANDIDATE_SQL, (today_id,)).fetchall()
            ready: list[Ticket] = []
            for row in rows:
                ticket = tickets_data.read_ticket(conn, str(row["id"]))
                if readiness.is_runnable(conn, ticket):
                    ready.append(ticket)
        finally:
            conn.close()
        for ticket in ready:
            self._employee_step_runner.run_ready_step(ticket.id)
        return [ticket.id for ticket in ready]

    def start(self, interval: int) -> None:
        """Start the production discovery thread."""
        if self._thread is not None:
            raise RuntimeError("Ticket readiness loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="ticket-readiness-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop discovery and join its thread so it cannot submit more ids."""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=10.0)
            self._thread = None

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _log.exception("ticket readiness poll failed")
            self._wake.wait(interval)
            self._wake.clear()
