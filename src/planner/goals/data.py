"""Stored goal rows; no page rules or conversation lifecycle live here."""

from __future__ import annotations

import json
import sqlite3
from typing import cast

from planner.core.contracts import ErrorCode, PlannerError
from planner.goals.contracts import Goal, GoalKind


def _goal(row: sqlite3.Row) -> Goal:
    return Goal(
        id=row["id"],
        project_id=row["project_id"],
        title=row["title"],
        why=row["why"],
        kind=cast(GoalKind, row["kind"]),
        active=bool(row["active"]),
        binary_checked=bool(row["completed"]),
        primary_checklist_block_id=row["primary_checklist_block_id"],
        deadline=row["deadline"],
        primary_chart=json.loads(row["primary_chart"]) if row["primary_chart"] else None,
        page=json.loads(row["page"]),
        revision=row["revision"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        employee_backend=row["employee_backend"],
        employee_launch_model=row["employee_launch_model"],
        employee_launch_reasoning_effort=row["employee_launch_reasoning_effort"],
    )


def read_goal(conn: sqlite3.Connection, goal_id: str) -> Goal:
    row = conn.execute("SELECT * FROM goals WHERE id=?", (goal_id,)).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.not_found, "goal not found")
    return _goal(row)


def list_goals(
    conn: sqlite3.Connection, *, active: bool | None = None, project_id: str | None = None
) -> list[Goal]:
    return [
        _goal(row)
        for row in conn.execute(
            "SELECT * FROM goals WHERE (? IS NULL OR active=?) "
            "AND (? IS NULL OR project_id=?) ORDER BY created_at,id",
            (active, active, project_id, project_id),
        )
    ]
