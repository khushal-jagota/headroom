"""Goal-owned files use goal existence and the ordinary goal authority boundary."""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from planner.core import authority
from planner.core.authority import require_above_or_self
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.files import managed_files
from planner.files.logic.paths import goal_files_root


def require_goal(conn: sqlite3.Connection, principal: Principal, goal_id: str) -> None:
    if conn.execute("SELECT 1 FROM goals WHERE id=?", (goal_id,)).fetchone() is None:
        raise PlannerError(ErrorCode.not_found, "goal not found", {})
    require_above_or_self(conn, principal, authority.goal(goal_id))


def write_file(
    conn: sqlite3.Connection,
    principal: Principal,
    goal_id: str,
    db_path: str,
    relative_path: str,
    content: str,
) -> dict[str, object]:
    require_goal(conn, principal, goal_id)
    root = managed_files.owner_root(
        goal_files_root(db_path), goal_id, create=True, label="goal"
    )
    assert root is not None
    managed_files.write_text(root, relative_path, content, "goal")
    return {
        "goal_id": goal_id,
        "path": relative_path,
        "url": f"/files/goals/{goal_id}/{quote(relative_path, safe='/')}",
    }


def delete_file(
    conn: sqlite3.Connection,
    principal: Principal,
    goal_id: str,
    db_path: str,
    relative_path: str,
) -> dict[str, object]:
    require_goal(conn, principal, goal_id)
    root = managed_files.owner_root(
        goal_files_root(db_path), goal_id, create=False, label="goal"
    )
    managed_files.delete(root, relative_path, "goal")
    return {"ok": True, "goal_id": goal_id, "path": relative_path}
