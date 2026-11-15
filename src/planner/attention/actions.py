"""One coherent historical attention read for HTTP, CLI, and daily collection."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from planner.attention import data
from planner.attention.contracts import Scope, ScopeKind, SummaryRequest
from planner.attention.logic.summary import build_summary, periods
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError


def read_summary(conn: sqlite3.Connection, request: SummaryRequest, *, now: int) -> JsonDict:
    try:
        current, previous = periods(request, now=now)
    except (ValueError, OverflowError) as error:
        raise PlannerError(ErrorCode.validation, str(error)) from error
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute("BEGIN")
    try:
        evidence = data.load_evidence(
            conn, first_at=previous["start_at"], last_at=current["end_at"]
        )
        result = build_summary(request, evidence, now=now)
        if request.scope.kind is ScopeKind.goal:
            goal = next(
                (
                    item
                    for item in evidence.destinations
                    if item.kind == "goal" and item.id == request.scope.id
                ),
                None,
            )
            if goal is not None and goal.project_id is not None:
                context = build_summary(
                    replace(request, scope=Scope(ScopeKind.project, goal.project_id)),
                    evidence,
                    now=now,
                )
                result["shared_project_context"] = {
                    "project_id": goal.project_id,
                    "totals": context["totals"],
                    "limitation": (
                        "Shared Project context overlaps across goals "
                        "and is not direct goal consumption."
                    ),
                }
            else:
                result["shared_project_context"] = None
        return result
    finally:
        if owns_transaction:
            conn.execute("ROLLBACK")
