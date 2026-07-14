"""Explicit inspection of a Ticket employee's authoritative Hermes history."""

from __future__ import annotations

import sqlite3

from planner.core.adapters.base import GatewayAdapter
from planner.core.errors import ErrorCode, PlannerError
from planner.tickets import data as tickets_data
from planner.tickets.contracts import EmployeeSessionHistory, EmployeeSessionIdTransition


def read_employee_session_history(
    conn: sqlite3.Connection,
    gateway: GatewayAdapter,
    ticket_id: str,
    now: int,
) -> EmployeeSessionHistory:
    """Read Hermes outside the write lock and persist only a current rotation winner."""
    while True:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        requested_id = ticket.employee_session_id
        if requested_id is None:
            return EmployeeSessionHistory(messages=(), employee_session_id=None)
        try:
            result = gateway.read_employee_session_history(requested_id, ticket_id)
        except PlannerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise PlannerError(
                ErrorCode.gateway_offline,
                "gateway unavailable",
                {"cause": str(exc), "ticket_id": ticket_id},
            ) from exc

        history_id = result.employee_session_id or requested_id
        conn.execute("BEGIN IMMEDIATE")
        try:
            winner = tickets_data.write_employee_session_id_in_transaction(
                conn,
                ticket_id,
                transition=EmployeeSessionIdTransition(
                    expected_employee_session_id=requested_id,
                    candidate_employee_session_id=history_id,
                ),
                force_fresh_employee_session=False,
                now=now,
            )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
        if winner == history_id:
            return EmployeeSessionHistory(
                messages=result.messages,
                employee_session_id=history_id,
            )
