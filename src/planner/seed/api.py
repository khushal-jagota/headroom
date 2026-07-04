"""Seed route (§9): POST /api/seed runs a migration and returns a MigrationReport."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request

from planner.core.errors import ErrorCode, PlannerError
from planner.seed.contracts import MigrationReport
from planner.seed.demo import seed_demo
from planner.seed.importer import seed_from_source

router = APIRouter()


def _demo_report(conn: sqlite3.Connection) -> MigrationReport:
    def count(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return MigrationReport(
        sprints=count("SELECT COUNT(*) FROM sprints"),
        sprint_items=count("SELECT COUNT(*) FROM sprint_items WHERE sprint_id IS NOT NULL"),
        deferred_items=count("SELECT COUNT(*) FROM sprint_items WHERE sprint_id IS NULL"),
        tickets=count("SELECT COUNT(*) FROM tickets"),
        ideas=count("SELECT COUNT(*) FROM ideas"),
        links=count("SELECT COUNT(*) FROM links WHERE kind = 'belongs_to'"),
        duplicates_skipped=0,
        skipped=[],
    )


@router.post("/seed")
async def seed(body: dict[str, Any], request: Request) -> dict[str, Any]:
    source_dir = body.get("source_dir")
    demo = bool(body.get("demo", False))
    has_source = source_dir is not None
    if has_source == demo:  # both present, or neither
        raise PlannerError(ErrorCode.validation, "provide exactly one of source_dir or demo")
    conn_factory: Callable[[], sqlite3.Connection] = request.app.state.conn_factory
    conn = conn_factory()
    try:
        if demo:
            seed_demo(conn)  # raises db_not_empty on a non-empty database
            report = _demo_report(conn)
        else:
            if not isinstance(source_dir, str) or not Path(source_dir).is_dir():
                raise PlannerError(
                    ErrorCode.validation, "source_dir must be an existing directory"
                )
            report = seed_from_source(conn, source_dir)
    finally:
        conn.close()
    return asdict(report)
