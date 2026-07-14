"""Read-only discovery of Tickets eligible for an automatic Employee step."""

from __future__ import annotations

import logging
import threading

from planner.core.clock import Clock
from planner.core.db import connect
from planner.days.logic import dates
from planner.runtime import automatic_employee_step_eligibility
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import configured_worker_type_registry

_log = logging.getLogger(__name__)

_CANDIDATE_SQL = (
    "SELECT t.id FROM tickets t JOIN day_tickets dt ON dt.ticket_id = t.id WHERE dt.day_id = ?"
)


class AutomaticEmployeeStepDiscoveryLoop:
    """Read-only discovery plus a wakeable periodic timer backstop."""

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
        """Ask discovery to poll now; the periodic timer remains the backstop."""
        self._wake.set()

    def poll_once(self) -> list[str]:
        """Discover eligible Ticket ids and pass them to the Employee-step runner."""
        planning_day_id = dates.resolve_day_id(
            "today",
            self._clock.now(),
            self._boundary_hour,
        )
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            rows = conn.execute(_CANDIDATE_SQL, (planning_day_id,)).fetchall()
            eligible_ticket_ids: list[str] = []
            registry = configured_worker_type_registry()
            for row in rows:
                ticket = tickets_data.read_ticket(conn, str(row["id"]))
                worker_type_definition = registry.require(ticket.worker_type)
                if automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step(
                    conn,
                    ticket,
                    planning_day_id=planning_day_id,
                    worker_type_definition=worker_type_definition,
                ):
                    eligible_ticket_ids.append(ticket.id)
        finally:
            conn.close()
        for ticket_id in eligible_ticket_ids:
            self._employee_step_runner.try_run_automatic_step(ticket_id)
        return eligible_ticket_ids

    def start(self, interval: int) -> None:
        """Start the production discovery thread."""
        if self._thread is not None:
            raise RuntimeError("automatic employee-step discovery loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="automatic-employee-step-discovery-loop",
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
                _log.exception("automatic employee-step discovery poll failed")
            self._wake.wait(interval)
            self._wake.clear()
