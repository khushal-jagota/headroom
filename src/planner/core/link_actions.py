"""Link actions that commit before waking Automatic Employee-step eligibility."""

from __future__ import annotations

import sqlite3

from planner.core import links
from planner.core.contracts import LinkKind
from planner.runtime.automatic_employee_step_eligibility_wake import (
    AutomaticEmployeeStepEligibilityWake,
)


def add_link(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    kind: LinkKind,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        links.add_link(conn, from_id, to_id, kind, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    if kind is LinkKind.blocks:
        automatic_employee_step_eligibility_wake.wake()


def remove_link(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    kind: LinkKind,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        links.remove_link(conn, from_id, to_id, kind, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    if kind is LinkKind.blocks:
        automatic_employee_step_eligibility_wake.wake()
