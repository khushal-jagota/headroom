"""Idea serialization and reads."""

import sqlite3

from planner.core.contracts import JsonDict


def idea_json(row: sqlite3.Row) -> JsonDict:
    return {
        "id": str(row["id"]),
        "title": str(row["title"]),
        "body": str(row["body"]),
        "project_id": str(row["project_id"]) if row["project_id"] is not None else None,
        "project": (str(row["project_name"]) if row["project_name"] is not None else None),
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


def list_ideas(conn: sqlite3.Connection, *, project_id: str | None = None) -> list[JsonDict]:
    where = "WHERE ideas.project_id = ?" if project_id is not None else ""
    params = (project_id,) if project_id is not None else ()
    sql = (
        "SELECT ideas.*, projects.name AS project_name "
        "FROM ideas LEFT JOIN projects ON projects.id = ideas.project_id "
        f"{where} "
        "ORDER BY ideas.created_at DESC, ideas.id"
    )
    rows = conn.execute(sql, params).fetchall()
    return [idea_json(r) for r in rows]
