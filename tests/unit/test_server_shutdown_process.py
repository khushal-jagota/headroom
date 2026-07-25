from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from planner.core.clock import RealClock
from planner.core.db import connect, create_schema
from planner.days import data as days_data
from planner.days.logic.dates import resolve_day_id
from planner.runtime.employee_step_runner import EmployeeStepRunner
from planner.runtime.step_gateway import EmployeeStepRunResult
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, TicketStatus


@dataclass(frozen=True)
class _Status:
    available: bool = True


class _BlockingGateway:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.released = threading.Event()
        self.interrupts: list[tuple[str, str]] = []

    def run_ticket_step(
        self,
        employee_session_id: str | None,
        entity_id: str,
        prompt_text: str,
        on_employee_session_id=None,
        *,
        require_existing_session: bool = False,
    ) -> EmployeeStepRunResult:
        del employee_session_id, prompt_text, require_existing_session
        if on_employee_session_id is not None:
            on_employee_session_id("shutdown-session")
        self.started.set()
        assert self.released.wait(5)
        return EmployeeStepRunResult("interrupted", "shutdown-session", None)

    def interrupt(
        self,
        employee_session_id: str,
        entity_id: str,
        *,
        deadline: float | None = None,
    ) -> None:
        del deadline
        self.interrupts.append((employee_session_id, entity_id))
        self.released.set()

    def status(self) -> _Status:
        return _Status()


def test_shutdown_interrupts_exact_bound_employee_step_without_partial_output(
    tmp_path: Path,
) -> None:
    db_path = str(tmp_path / "shutdown.db")
    clock = RealClock()
    with connect(db_path) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Shutdown",
            actor="human",
            now=clock.now_unix(),
            title_max_chars=200,
        )
        tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=clock.now_unix(),
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        days_data.add_day_ticket(
            conn,
            resolve_day_id("today", clock.now(), 5),
            ticket.id,
            clock.now_unix(),
        )

    gateway = _BlockingGateway()
    runner = EmployeeStepRunner(
        db_path,
        clock,
        gateway=gateway,
        boundary_hour=5,
    )
    runner.try_run_automatic_step(ticket.id)
    assert gateway.started.wait(5)
    runner.stop(deadline=monotonic() + 5)

    assert gateway.interrupts == [("shutdown-session", ticket.id)]
    with connect(db_path) as conn:
        run = conn.execute(
            "SELECT * FROM employee_step_runs WHERE ticket_id = ?", (ticket.id,)
        ).fetchone()
        stored = tickets_data.read_ticket(conn, ticket.id)
    assert run is not None
    assert run["status"] == "running"
    assert set(run.keys()) == {
        "employee_step_id",
        "ticket_id",
        "status",
        "employee_session_id",
        "error",
        "started_at",
        "updated_at",
        "completed_at",
    }
    assert stored.ticket_status is TicketStatus.agent
    assert stored.employee_session_id == "shutdown-session"
