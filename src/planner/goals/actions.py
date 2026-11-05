"""The single writer for goal facts and saved page JSON."""

from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import date
from typing import Any, cast, get_type_hints

from planner.core import authority
from planner.core.contracts import ErrorCode, PlannerError, Principal
from planner.core.ids import new_id
from planner.datasets.data import read_dataset
from planner.goals import data
from planner.goals.calendar import server_date
from planner.goals.contracts import GOAL_REQUIRED_TEXT, ChartBlock, Goal, GoalWriteFields
from planner.goals.logic.categories import validate_category_references
from planner.goals.logic.completion import completion_date_after_edit, prepare_completion
from planner.goals.logic.documents import (
    blocks,
    charts,
    dataset_ids,
    expectation_measure_ids,
    measure_ids,
    prepare_document,
    validate_chart,
)
from planner.goals.rule_evaluation import evaluate_completion_date, parse_completion_rule
from planner.measures.contracts import MeasureOrigin
from planner.measures.data import read_definitions, read_measure, transaction
from planner.measures.logic.formulas import validate_graph
from planner.worker_settings.service import read_chief_settings

_EDITABLE = set(get_type_hints(GoalWriteFields, include_extras=True))


def _accepted(
    conn: sqlite3.Connection, current: Goal, body: dict[str, Any], *,
    now: int, creating: bool = False
) -> Goal:
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
        validate_chart(cast(ChartBlock, chart), "$.primary_chart")
        if chart["placement"] != "primary":
            raise ValueError("primary chart must have primary placement")
        changes["primary_chart"] = chart
    if "completion_rule" in changes and changes["completion_rule"] is not None:
        changes["completion_rule"] = parse_completion_rule(conn, changes["completion_rule"])
    result = replace(current, **changes)
    if current.kind != result.kind and result.kind == "binary":
        result = replace(result, completion_rule=None)
    if result.holder_kind not in ("chief", "manager"):
        raise ValueError("holder_kind must be chief or manager")
    if result.holder_kind == "chief" and result.holder_id is not None:
        raise ValueError("Chief holder_id must be null")
    if result.holder_kind == "manager" and (
        not result.holder_id
        or conn.execute("SELECT 1 FROM managers WHERE id=?", (result.holder_id,)).fetchone() is None
    ):
        raise ValueError("goal holder Manager does not exist")
    for key in GOAL_REQUIRED_TEXT:
        value = getattr(result, key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be nonempty")
    if not isinstance(result.why, str):
        raise ValueError("why must be text")
    if creating and "kind" not in body:
        raise ValueError(
            "kind is required: choose binary or quantitative and its main display; "
            "inspect panels goal schema create --json"
        )
    if result.kind not in ("binary", "quantitative"):
        operation = "create" if creating else "edit"
        raise ValueError(
            "unknown goal kind: choose binary or quantitative; "
            f"inspect panels goal schema {operation} --json"
        )
    if type(result.active) is not bool:
        raise ValueError("active and completed must be booleans")
    result = prepare_completion(
        current,
        result,
        creating=creating,
        selection_explicit="primary_checklist_block_id" in body,
        chart_explicit="primary_chart" in body,
        completed=None if result.completion_rule is not None else body.get("completed"),
    )
    if result.kind == "binary" and result.completion_rule is not None:
        raise ValueError("completion_rule applies only to quantitative goals")
    if result.kind == "quantitative" and result.completion_rule is None:
        if creating or current.kind != "quantitative" or current.completion_rule is not None:
            operation = "create" if creating else "edit"
            raise ValueError(
                "completion_rule is required for quantitative goals; "
                f"inspect panels goal schema {operation} --json"
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
    dataset_definitions = {
        identifier: read_dataset(conn, identifier)
        for identifier in dataset_ids(result.primary_chart, result.page)
    }
    for identifier in graph_roots:
        validate_graph(identifier, definitions)
    for chart in charts(result.primary_chart, result.page):
        if "binding" in chart:
            validate_category_references(chart, datasets=dataset_definitions, measures=definitions)
        if (
            "manual" in chart
            and read_measure(conn, chart["measure_id"]).origin is not MeasureOrigin.manual
        ):
            raise ValueError("manual controls require a manual-origin measure")
    if result.completion_rule is not None:
        if creating or result.completion_rule != current.completion_rule or "completed" in body:
            completion_date = evaluate_completion_date(conn, result.completion_rule, now=now)
            result = replace(result, completion_date=completion_date)
        if "completed" in body and body["completed"] != (result.completion_date is not None):
            raise ValueError("completion follows the goal's completion_rule")
    else:
        result = replace(result, completion_date=completion_date_after_edit(
            current, result, today=server_date(now),
            creating=creating,
        ))
    return result


def _values(goal: Goal) -> dict[str, Any]:
    values = asdict(goal)
    values["completed"] = values.pop("binary_checked")
    values["page"] = json.dumps(goal.page, ensure_ascii=False, separators=(",", ":"))
    values["primary_chart"] = (
        json.dumps(goal.primary_chart, separators=(",", ":")) if goal.primary_chart else None
    )
    values["completion_rule"] = (
        json.dumps(goal.completion_rule, separators=(",", ":")) if goal.completion_rule else None
    )
    return values


def create_goal(
    conn: sqlite3.Connection, body: dict[str, Any], *, principal: Principal, now: int
) -> Goal:
    with transaction(conn):
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
            accepted = _accepted(conn, current, body, now=now, creating=True)
        except (ValueError, TypeError) as exc:
            raise PlannerError(ErrorCode.validation, str(exc)) from exc
        authority.require_above_or_self(
            conn, principal,
            authority.manager(cast(str, accepted.holder_id))
            if accepted.holder_kind == "manager"
            else authority.owner_only("goals"),
        )
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
                _accepted(conn, current, body, now=now),
                revision=current.revision + 1, updated_at=now
            )
        except (ValueError, TypeError) as exc:
            raise PlannerError(ErrorCode.validation, str(exc)) from exc
        authority.require_above_or_self(
            conn, principal,
            authority.manager(cast(str, accepted.holder_id))
            if accepted.holder_kind == "manager"
            else authority.owner_only("goals"),
        )
        values = _values(accepted)
        values.pop("id")
        conn.execute(
            f"UPDATE goals SET {','.join(key + '=?' for key in values)} WHERE id=?",
            (*values.values(), goal_id),
        )
    return accepted


def make_manager(
    conn: sqlite3.Connection,
    goal_id: str,
    *,
    expected_revision: object,
    principal: Principal,
    clock: Any,
) -> tuple[str, Goal]:
    from planner.managers.data import create_manager

    with transaction(conn):
        authority.require_above_or_self(conn, principal, authority.goal(goal_id))
        current = data.read_goal(conn, goal_id)
        if current.holder_kind != "chief":
            raise PlannerError(ErrorCode.validation, "only a Chief goal can become a Manager")
        if type(expected_revision) is not int or expected_revision != current.revision:
            raise PlannerError(
                ErrorCode.stale_claim, "goal changed; read the accepted document before promotion"
            )
        manager = create_manager(
            conn, title=current.title, project_id=current.project_id, clock=clock
        )
        accepted = update_goal(
            conn,
            goal_id,
            {
                "expected_revision": current.revision,
                "holder_kind": "manager",
                "holder_id": manager.id,
            },
            principal=principal,
            now=clock.now_unix(),
        )
    return manager.id, accepted
