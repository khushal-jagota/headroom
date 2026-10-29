"""The single writer for goal facts and saved page JSON."""

from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import date
from typing import Any, cast

from planner.core import authority
from planner.core.contracts import ErrorCode, PlannerError, Principal
from planner.core.ids import new_id
from planner.goals import data
from planner.goals.contracts import ChartBlock, Goal
from planner.goals.logic.completion import prepare_completion
from planner.goals.logic.documents import (
    blocks,
    charts,
    expectation_measure_ids,
    measure_ids,
    prepare_document,
    validate_chart,
)
from planner.measures.contracts import MeasureOrigin
from planner.measures.data import read_definitions, read_measure, transaction
from planner.measures.logic.formulas import validate_graph
from planner.worker_settings.service import read_chief_settings

_EDITABLE = {
    "project_id",
    "title",
    "why",
    "kind",
    "active",
    "completed",
    "primary_checklist_block_id",
    "deadline",
    "primary_chart",
    "page",
}


def _accepted(conn: sqlite3.Connection, current: Goal, body: dict[str, Any]) -> Goal:
    if set(body) - _EDITABLE - {"expected_revision"}:
        raise ValueError("unknown goal field")
    changes = {key: value for key, value in body.items() if key in _EDITABLE and key != "completed"}
    if "completed" in body and type(body["completed"]) is not bool:
        raise ValueError("active and completed must be booleans")
    if "page" in changes:
        changes["page"] = prepare_document(changes["page"], lambda: new_id("block"))
    if changes.get("primary_chart") is not None:
        chart = copy.deepcopy(changes["primary_chart"])
        if not isinstance(chart, dict):
            raise ValueError("primary chart must be an object")
        chart.setdefault("id", new_id("chart"))
        chart.setdefault("placement", "primary")
        validate_chart(cast(ChartBlock, chart))
        if chart["placement"] != "primary":
            raise ValueError("primary chart must have primary placement")
        changes["primary_chart"] = chart
    result = replace(current, **changes)
    for key in ("title", "project_id"):
        value = getattr(result, key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be nonempty")
    if not isinstance(result.why, str):
        raise ValueError("why must be text")
    if result.kind not in ("binary", "quantitative"):
        raise ValueError("unknown goal kind")
    if type(result.active) is not bool:
        raise ValueError("active and completed must be booleans")
    result = prepare_completion(
        current,
        result,
        selection_explicit="primary_checklist_block_id" in body,
        completed=body.get("completed"),
    )
    if result.deadline is not None:
        if (
            not isinstance(result.deadline, str)
            or date.fromisoformat(result.deadline).isoformat() != result.deadline
        ):
            raise ValueError("deadline must be an ISO calendar date")
    if conn.execute("SELECT 1 FROM projects WHERE id=?", (result.project_id,)).fetchone() is None:
        raise ValueError("goal Project does not exist")
    if result.primary_chart and result.primary_chart["id"] in {
        item["id"] for item in blocks(result.page)
    }:
        raise ValueError("primary chart ID must be unique")
    for identifier in measure_ids(result.primary_chart, result.page):
        read_measure(conn, identifier)
    expectation_inputs = tuple(expectation_measure_ids(result.primary_chart, result.page))
    graph_roots = tuple(
        set(expectation_inputs) | set(measure_ids(result.primary_chart, result.page))
    )
    definitions = read_definitions(conn, graph_roots)
    for identifier in graph_roots:
        validate_graph(identifier, definitions)
    for chart in charts(result.primary_chart, result.page):
        if (
            "manual" in chart
            and read_measure(conn, chart["measure_id"]).origin is not MeasureOrigin.manual
        ):
            raise ValueError("manual controls require a manual-origin measure")
    return result


def _values(goal: Goal) -> dict[str, Any]:
    values = asdict(goal)
    values["completed"] = values.pop("binary_checked")
    values["page"] = json.dumps(goal.page, ensure_ascii=False, separators=(",", ":"))
    values["primary_chart"] = (
        json.dumps(goal.primary_chart, separators=(",", ":")) if goal.primary_chart else None
    )
    return values


def create_goal(
    conn: sqlite3.Connection, body: dict[str, Any], *, principal: Principal, now: int
) -> Goal:
    with transaction(conn):
        authority.require_above(conn, principal, authority.owner_only("goals"))
        defaults = read_chief_settings(conn).launch_defaults
        title = body.get("title", "")
        current = Goal(
            id=new_id("goal"),
            project_id=body.get("project_id", ""),
            title=title,
            why="",
            kind="binary",
            active=True,
            binary_checked=False,
            primary_checklist_block_id=None,
            deadline=None,
            primary_chart=None,
            page={
                "kind": "headroom.page",
                "version": 1,
                "title": title,
                "summary": "",
                "blocks": [],
            },
            revision=1,
            created_at=now,
            updated_at=now,
            employee_backend=defaults.employee_backend,
            employee_launch_model=defaults.employee_launch_model,
            employee_launch_reasoning_effort=defaults.employee_launch_reasoning_effort,
        )
        try:
            accepted = _accepted(conn, current, body)
        except (ValueError, TypeError) as exc:
            raise PlannerError(ErrorCode.validation, str(exc)) from exc
        values = _values(accepted)
        fields = ",".join(values)
        conn.execute(
            f"INSERT INTO goals({fields}) VALUES({','.join('?' for _ in values)})",
            tuple(values.values()),
        )
    return accepted


def update_goal(
    conn: sqlite3.Connection, goal_id: str, body: dict[str, Any], *, principal: Principal, now: int
) -> Goal:
    with transaction(conn):
        current = data.read_goal(conn, goal_id)
        authority.require_above_or_self(conn, principal, authority.goal(goal_id))
        if (
            type(body.get("expected_revision")) is not int
            or body["expected_revision"] != current.revision
        ):
            raise PlannerError(
                ErrorCode.stale_claim,
                "goal changed; read the accepted document before editing again",
            )
        try:
            accepted = replace(
                _accepted(conn, current, body), revision=current.revision + 1, updated_at=now
            )
        except (ValueError, TypeError) as exc:
            raise PlannerError(ErrorCode.validation, str(exc)) from exc
        values = _values(accepted)
        values.pop("id")
        conn.execute(
            f"UPDATE goals SET {','.join(key + '=?' for key in values)} WHERE id=?",
            (*values.values(), goal_id),
        )
    return accepted
