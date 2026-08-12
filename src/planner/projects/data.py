"""Project catalog reads and writes."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from planner.core.contracts import JsonDict, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.list_reads.contracts import ListPage, ListPageRequest
from planner.projects.contracts import Project

DEFAULT_PROJECTS: tuple[tuple[str, str], ...] = (
    ("project_vylo", "Vylo"),
    ("project_tribe", "Tribe"),
    ("project_other", "Other"),
    ("project_personal", "Personal"),
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _row_to_project(row: sqlite3.Row) -> Project:
    return Project(
        id=str(row["id"]),
        name=str(row["name"]),
        summary=str(row["summary"]),
        priority=Priority(str(row["priority"])) if row["priority"] is not None else None,
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


def project_json(project: Project) -> JsonDict:
    return {
        "id": project.id,
        "name": project.name,
        "summary": project.summary,
        "priority": project.priority.value if project.priority is not None else None,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def project_id_for_name(name: str) -> str:
    slug = _SLUG_RE.sub("_", name.strip().lower()).strip("_")
    if not slug:
        raise PlannerError(ErrorCode.validation, "project name is required", {})
    return f"project_{slug}"


def seed_default_projects(conn: sqlite3.Connection) -> None:
    for project_id, name in DEFAULT_PROJECTS:
        conn.execute(
            "INSERT OR IGNORE INTO projects (id, name, created_at, updated_at) VALUES (?, ?, 0, 0)",
            (project_id, name),
        )


def list_projects(conn: sqlite3.Connection) -> list[Project]:
    rows = conn.execute(
        "SELECT id, name, summary, priority, created_at, updated_at "
        "FROM projects ORDER BY lower(name), id"
    ).fetchall()
    return [_row_to_project(row) for row in rows]


def list_project_summaries(
    conn: sqlite3.Connection, *, page_request: ListPageRequest
) -> ListPage[JsonDict]:
    rows = conn.execute(
        "SELECT id, name, priority FROM projects ORDER BY lower(name), id"
    ).fetchall()
    summaries = [
        {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "priority": str(row["priority"]) if row["priority"] is not None else None,
        }
        for row in rows[page_request.offset : page_request.offset + page_request.limit]
    ]
    return ListPage(
        rows=tuple(summaries),
        match_count=len(rows),
        limit=page_request.limit,
        offset=page_request.offset,
    )


def read_project(conn: sqlite3.Connection, project_id: str) -> Project:
    row = conn.execute(
        "SELECT id, name, summary, priority, created_at, updated_at "
        "FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.validation, "invalid project_id", {"project_id": project_id})
    return _row_to_project(row)


def read_project_by_name(conn: sqlite3.Connection, name: str) -> Project:
    row = conn.execute(
        "SELECT id, name, summary, priority, created_at, updated_at "
        "FROM projects WHERE name = ? COLLATE NOCASE",
        (name.strip(),),
    ).fetchone()
    if row is None:
        raise PlannerError(ErrorCode.validation, "invalid project", {"project": name})
    return _row_to_project(row)


def resolve_project(
    conn: sqlite3.Connection,
    *,
    project_id: str | None,
    project_name: str | None,
    required: bool = False,
) -> Project | None:
    by_id = read_project(conn, project_id) if project_id is not None else None
    by_name = read_project_by_name(conn, project_name) if project_name is not None else None
    if by_id is not None and by_name is not None and by_id.id != by_name.id:
        raise PlannerError(
            ErrorCode.validation,
            "project_id and project do not match",
            {"project_id": project_id, "project": project_name},
        )
    project = by_id or by_name
    if project is None and required:
        raise PlannerError(ErrorCode.validation, "project is required", {})
    return project


def create_project(
    conn: sqlite3.Connection,
    *,
    name: str,
    priority: Priority,
    summary: str = "",
    now: int,
) -> Project:
    clean_name = name.strip()
    clean_summary = summary.strip()
    if not clean_name:
        raise PlannerError(ErrorCode.validation, "project name is required", {})

    with _tx(conn):
        if (
            conn.execute(
                "SELECT 1 FROM projects WHERE name = ? COLLATE NOCASE", (clean_name,)
            ).fetchone()
            is not None
        ):
            raise PlannerError(ErrorCode.validation, "project already exists", {"name": clean_name})

        base_id = project_id_for_name(clean_name)
        project_id = base_id
        suffix = 2
        while (
            conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone()
            is not None
        ):
            project_id = f"{base_id}_{suffix}"
            suffix += 1
        conn.execute(
            "INSERT INTO projects (id, name, summary, priority, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (project_id, clean_name, clean_summary, priority.value, now, now),
        )
    return read_project(conn, project_id)


def update_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    name: str | None = None,
    summary: str | None = None,
    priority: Priority | None = None,
    now: int,
) -> Project:
    updates: dict[str, str] = {}
    if name is not None:
        clean_name = name.strip()
        if not clean_name:
            raise PlannerError(ErrorCode.validation, "project name is required", {})
        updates["name"] = clean_name
    if summary is not None:
        updates["summary"] = summary.strip()
    if priority is not None:
        updates["priority"] = priority.value
    if not updates:
        raise PlannerError(ErrorCode.validation, "no project fields to update", {})

    with _tx(conn):
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise PlannerError(
                ErrorCode.validation, "invalid project_id", {"project_id": project_id}
            )
        if "name" in updates:
            existing = conn.execute(
                "SELECT id FROM projects WHERE name = ? COLLATE NOCASE", (updates["name"],)
            ).fetchone()
            if existing is not None and str(existing["id"]) != project_id:
                raise PlannerError(
                    ErrorCode.validation, "project already exists", {"name": updates["name"]}
                )
        assignments = ", ".join(f"{field} = ?" for field in updates)
        params = [*updates.values(), now, project_id]
        conn.execute(f"UPDATE projects SET {assignments}, updated_at = ? WHERE id = ?", params)
    return read_project(conn, project_id)
