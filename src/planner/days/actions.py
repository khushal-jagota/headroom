"""Day membership actions that commit before waking Automatic Employee-step eligibility."""

from __future__ import annotations

import sqlite3

from planner.days import data as days_data
from planner.runtime.automatic_employee_step_eligibility_wake import (
    AutomaticEmployeeStepEligibilityWake,
)
from planner.tickets import data as tickets_data


def add_ticket_to_day(
    conn: sqlite3.Connection,
    day_id: str,
    ticket_id: str,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        tickets_data.read_ticket(conn, ticket_id)
        changed = days_data.add_day_ticket(conn, day_id, ticket_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    if changed:
        automatic_employee_step_eligibility_wake.wake()
    return changed


def remove_ticket_from_day(
    conn: sqlite3.Connection,
    day_id: str,
    ticket_id: str,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> bool:
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = days_data.remove_day_ticket(conn, day_id, ticket_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    if changed:
        automatic_employee_step_eligibility_wake.wake()
    return changed
