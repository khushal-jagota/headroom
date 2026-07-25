"""Durable correctness records for Employee steps.

The rows in this module describe only execution ownership and settlement.  They
are deliberately not a transcript and contain no model input or output.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal

from planner.core.ids import new_id

EmployeeStepStatus = Literal["running", "complete", "interrupted", "errored"]


@dataclass(frozen=True, slots=True)
class EmployeeStepRun:
    employee_step_id: str
    ticket_id: str
    status: EmployeeStepStatus
    employee_session_id: str | None
    error: str | None
    started_at: int
    updated_at: int
    completed_at: int | None


class SqliteEmployeeStepRepository:
    """The sole SQL owner of ``employee_step_runs``.

    Every method uses the caller's connection.  This lets Ticket, binding,
    permission and migration owners include a step mutation in their existing
    transaction rather than weakening it with a second connection.
    """

    @staticmethod
    def _from_row(row: sqlite3.Row) -> EmployeeStepRun:
        return EmployeeStepRun(
            employee_step_id=str(row["employee_step_id"]),
            ticket_id=str(row["ticket_id"]),
            status=row["status"],
            employee_session_id=row["employee_session_id"],
            error=row["error"],
            started_at=int(row["started_at"]),
            updated_at=int(row["updated_at"]),
            completed_at=row["completed_at"],
        )

    def start(
        self,
        conn: sqlite3.Connection,
        ticket_id: str,
        *,
        now: int,
        employee_session_id: str | None = None,
    ) -> EmployeeStepRun:
        employee_step_id = new_id("run")
        conn.execute(
            "INSERT INTO employee_step_runs ("
            "employee_step_id, ticket_id, status, employee_session_id, error, "
            "started_at, updated_at, completed_at"
            ") VALUES (?, ?, 'running', ?, NULL, ?, ?, NULL)",
            (employee_step_id, ticket_id, employee_session_id, now, now),
        )
        return self.require(conn, employee_step_id)

    def require(
        self, conn: sqlite3.Connection, employee_step_id: str
    ) -> EmployeeStepRun:
        row = conn.execute(
            "SELECT * FROM employee_step_runs WHERE employee_step_id = ?",
            (employee_step_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Employee step not found: {employee_step_id}")
        return self._from_row(row)

    def read_running(
        self, conn: sqlite3.Connection, ticket_id: str
    ) -> EmployeeStepRun | None:
        row = conn.execute(
            "SELECT * FROM employee_step_runs "
            "WHERE ticket_id = ? AND status = 'running'",
            (ticket_id,),
        ).fetchone()
        return None if row is None else self._from_row(row)

    def running_exists(self, conn: sqlite3.Connection, ticket_id: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM employee_step_runs "
                "WHERE ticket_id = ? AND status = 'running'",
                (ticket_id,),
            ).fetchone()
            is not None
        )

    def bind_session(
        self,
        conn: sqlite3.Connection,
        employee_step_id: str,
        *,
        ticket_id: str,
        employee_session_id: str,
        now: int,
    ) -> EmployeeStepRun | None:
        cursor = conn.execute(
            "UPDATE employee_step_runs SET employee_session_id = ?, updated_at = ? "
            "WHERE employee_step_id = ? AND ticket_id = ? AND status = 'running' "
            "AND (employee_session_id IS NULL OR employee_session_id = ?)",
            (
                employee_session_id,
                now,
                employee_step_id,
                ticket_id,
                employee_session_id,
            ),
        )
        if cursor.rowcount != 1:
            return None
        return self.require(conn, employee_step_id)

    def settle(
        self,
        conn: sqlite3.Connection,
        employee_step_id: str,
        *,
        ticket_id: str,
        status: EmployeeStepStatus,
        error: str | None,
        now: int,
    ) -> EmployeeStepRun | None:
        if status == "running":
            raise ValueError("terminal settlement cannot use running status")
        cursor = conn.execute(
            "UPDATE employee_step_runs SET status = ?, error = ?, updated_at = ?, "
            "completed_at = ? WHERE employee_step_id = ? AND ticket_id = ? "
            "AND status = 'running'",
            (status, error, now, now, employee_step_id, ticket_id),
        )
        if cursor.rowcount != 1:
            return None
        return self.require(conn, employee_step_id)

    def replace_running_for_restart(
        self,
        conn: sqlite3.Connection,
        ticket_id: str,
        *,
        expected_employee_session_id: str,
        now: int,
    ) -> EmployeeStepRun | None:
        running = self.read_running(conn, ticket_id)
        if (
            running is None
            or running.employee_session_id != expected_employee_session_id
        ):
            return None
        settled = self.settle(
            conn,
            running.employee_step_id,
            ticket_id=ticket_id,
            status="interrupted",
            error=None,
            now=now,
        )
        if settled is None:
            return None
        return self.start(
            conn,
            ticket_id,
            now=now,
            employee_session_id=expected_employee_session_id,
        )

    def interrupt_stale_handoff(
        self,
        conn: sqlite3.Connection,
        ticket_id: str,
        *,
        now: int,
        error: str | None = None,
    ) -> EmployeeStepRun | None:
        running = self.read_running(conn, ticket_id)
        if running is None:
            return None
        return self.settle(
            conn,
            running.employee_step_id,
            ticket_id=ticket_id,
            status="interrupted",
            error=error,
            now=now,
        )

    def interrupt_all_stale_handoffs(
        self,
        conn: sqlite3.Connection,
        *,
        now: int,
    ) -> tuple[EmployeeStepRun, ...]:
        """Interrupt running steps whose Ticket no longer carries running ownership."""
        rows = conn.execute(
            "SELECT employee_step_runs.employee_step_id, employee_step_runs.ticket_id "
            "FROM employee_step_runs "
            "JOIN tickets ON tickets.id = employee_step_runs.ticket_id "
            "WHERE employee_step_runs.status = 'running' "
            "AND tickets.ticket_status <> 'agent' "
            "ORDER BY employee_step_runs.started_at, employee_step_runs.employee_step_id"
        ).fetchall()
        settled: list[EmployeeStepRun] = []
        for row in rows:
            run = self.settle(
                conn,
                str(row["employee_step_id"]),
                ticket_id=str(row["ticket_id"]),
                status="interrupted",
                error=None,
                now=now,
            )
            if run is not None:
                settled.append(run)
        return tuple(settled)
