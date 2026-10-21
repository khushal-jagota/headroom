"""Canonical Idea writer."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import ID_PREFIXES, new_id


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    if conn.in_transaction:
        conn.execute("SAVEPOINT idea_write")
        try:
            yield
        except BaseException:
            conn.execute("ROLLBACK TO idea_write")
            conn.execute("RELEASE idea_write")
            raise
        else:
            conn.execute("RELEASE idea_write")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def create_idea(
    conn: sqlite3.Connection,
    *,
    title: str,
    body: str,
    project_id: str | None,
    now: int,
) -> sqlite3.Row:
    if not title:
        raise PlannerError(ErrorCode.validation, "idea title is required", {})
    idea_id = new_id(ID_PREFIXES["idea"])
    with _tx(conn):
        conn.execute(
            "INSERT INTO ideas (id, title, body, project_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (idea_id, title, body, project_id, now, now),
        )
    row = cast(
        sqlite3.Row | None,
        conn.execute(
            "SELECT ideas.*, projects.name AS project_name "
            "FROM ideas LEFT JOIN projects ON projects.id = ideas.project_id "
            "WHERE ideas.id = ?",
            (idea_id,),
        ).fetchone(),
    )
    assert row is not None
    return row
