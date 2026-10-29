"""Read the saved page separately from dated measure results."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

from planner.core.clock import Clock, RealClock
from planner.core.contracts import JsonDict, PlannerError
from planner.goals import configuration as limits
from planner.goals import data
from planner.goals.contracts import (
    Goal,
    GoalResponse,
    GoalsResponse,
    ScalarChartProjection,
)
from planner.goals.logic.documents import charts, expectation_measure_ids, measure_ids
from planner.goals.logic.projection import (
    projection_bounds,
    projection_point,
    projection_status,
    scalar_projection,
)
from planner.goals.logic.status import (
    calendar_projection,
    chart_status,
    goal_status,
)
from planner.measures.actions import read_values
from planner.measures.contracts import (
    MeasureDate,
    MeasureOrigin,
    MeasureReadStatus,
    MeasureValue,
    ReadMeasuresBody,
)
from planner.measures.data import read_definitions, read_demanded, read_measure, transaction
from planner.measures.logic.dates import date_range, invalid
from planner.measures.logic.evaluation import (
    Evaluation,
    demanded_pairs,
    expression_references,
    phase_changed,
)
from planner.measures.logic.formulas import parse_formula


def calendar_now(clock: Clock) -> datetime:
    now = clock.now()
    if isinstance(clock, RealClock):
        # Resolve local calendar rules at the I/O edge, retaining DST transitions.
        zone = os.environ.get("TZ")
        if zone:
            return now.astimezone(ZoneInfo(zone.removeprefix(":")))
        with Path("/etc/localtime").open("rb") as stream:
            return now.astimezone(ZoneInfo.from_file(stream))
    return now


def goal_json(conn: sqlite3.Connection, goal: Goal, now: datetime) -> GoalResponse:
    today, midnight = calendar_projection(now)
    agent = conn.execute(
        "SELECT conversation_id FROM agents WHERE agent_key=?", (f"goal_{goal.id}",)
    ).fetchone()
    return cast(
        GoalResponse,
        dict(
            **asdict(goal),
            conversation_id=agent["conversation_id"] if agent else None,
            status=goal_status(goal.completed, goal.deadline, today)
            if goal.kind == "binary"
            else "neutral",
            status_date=today,
            next_refresh_at=midnight,
        ),
    )


def list_json(
    conn: sqlite3.Connection,
    now: datetime,
    *,
    active: bool | None = None,
    project_id: str | None = None,
) -> GoalsResponse:
    today, midnight = calendar_projection(now)
    return {
        "goals": [
            goal_json(conn, row, now)
            for row in data.list_goals(conn, active=active, project_id=project_id)
        ],
        "status_date": today,
        "next_refresh_at": midnight,
    }


def reading_calendar(conn: sqlite3.Connection, goal: Goal, now: datetime) -> tuple[str, int]:
    today, midnight = calendar_projection(now)
    dates = [today]
    definitions = {
        identifier: read_measure(conn, identifier)
        for identifier in measure_ids(goal.primary_chart, goal.page)
    }
    definitions.update(
        read_definitions(
            conn,
            tuple(expectation_measure_ids(goal.primary_chart, goal.page)),
            missing_ok=True,
        )
    )
    for identifier, definition in sorted(definitions.items()):
        local_day, next_day = calendar_projection(now.astimezone(ZoneInfo(definition.timezone)))
        dates.append(identifier + ":" + local_day)
        midnight = min(midnight, next_day)
    return "|".join(dates), midnight


def _earliest_date(conn: sqlite3.Connection, identifier: str) -> str | None:
    return cast(
        str | None,
        conn.execute(
            "WITH RECURSIVE inputs(id) AS (SELECT ? UNION "
            "SELECT input_measure_id FROM measure_dependencies "
            "JOIN inputs ON measure_id=id) "
            "SELECT MIN(date) FROM (SELECT date FROM measure_observations "
            "WHERE measure_id IN inputs "
            "UNION ALL SELECT date FROM measure_history_gaps WHERE measure_id IN inputs)",
            (identifier,),
        ).fetchone()[0],
    )


def _formula_projections(
    conn: sqlite3.Connection,
    goal: Goal,
    now: datetime,
    first_date: str | None,
    last_date: str | None,
) -> tuple[dict[str, ScalarChartProjection], dict[str, JsonDict]]:
    formula_charts = [
        chart for chart in charts(goal.primary_chart, goal.page) if "expectation" in chart
    ]
    # Chart JSON reloads enum values as strings. Restore the measurement contract.
    formula_charts = [chart.copy() for chart in formula_charts]
    for chart in formula_charts:
        chart["expectation"] = parse_formula(chart["expectation"])
    if not formula_charts:
        return {}, {}
    bound_ids = tuple(sorted({chart["measure_id"] for chart in formula_charts}))
    bound_definitions = {identifier: read_measure(conn, identifier) for identifier in bound_ids}
    bounds = {}
    point_count = 0
    for chart in formula_charts:
        definition = bound_definitions[chart["measure_id"]]
        today = now.astimezone(ZoneInfo(definition.timezone)).date().isoformat()
        first, last, length = projection_bounds(
            today, _earliest_date(conn, definition.id), goal.deadline, first_date, last_date
        )
        point_count += length
        if point_count > limits.MAX_PROJECTED_CHART_DATES:
            raise invalid(
                f"goal projections exceed {limits.MAX_PROJECTED_CHART_DATES} chart-date points; "
                "request a narrower date range"
            )
        bounds[chart["id"]] = (first, last, today)

    # Check every chart's bounds before any date interval is allocated.
    definitions = read_definitions(conn, bound_ids, missing_ok=True)
    definitions.update(
        read_definitions(
            conn,
            tuple(expectation_measure_ids(goal.primary_chart, goal.page)),
            missing_ok=True,
        )
    )
    instant = int(now.timestamp())
    roots: set[MeasureDate] = set()
    days_by_chart = {}
    for chart in formula_charts:
        first, last, today = bounds[chart["id"]]
        days = date_range(first, last)
        days_by_chart[chart["id"]] = days
        definition = bound_definitions[chart["measure_id"]]
        for day in days:
            if day <= today or definition.origin is not MeasureOrigin.assembled:
                roots.add(MeasureDate(definition.id, day))
            try:
                references = expression_references(
                    chart["expectation"], day, definition.timezone, instant
                )
            except (PlannerError, RecursionError):
                references = ()  # Evaluation preserves date and arithmetic-depth failures.
            for pair in references:
                roots.add(pair)
                if len(roots) > limits.MAX_DEMANDED_MEASURE_DATES:
                    raise invalid(
                        f"goal projection exceeds {limits.MAX_DEMANDED_MEASURE_DATES} demanded "
                        "measure/date pairs; request a narrower date range"
                    )
    try:
        pairs = demanded_pairs(
            tuple(roots),
            definitions,
            now=instant,
            limit=limits.MAX_DEMANDED_MEASURE_DATES,
            missing_ok=True,
        )
    except RecursionError:
        # Preserve ordinary inputs when another root exceeds interpreter depth.
        # Evaluation returns arithmetic_limit for the unexpanded deep root.
        pairs = set()
        for root in roots:
            try:
                pairs.update(
                    demanded_pairs(
                        (root,),
                        definitions,
                        now=instant,
                        limit=limits.MAX_DEMANDED_MEASURE_DATES,
                        missing_ok=True,
                    )
                )
            except RecursionError:
                continue
            if len(pairs) > limits.MAX_DEMANDED_MEASURE_DATES:
                raise invalid(
                    f"goal projection exceeds {limits.MAX_DEMANDED_MEASURE_DATES} demanded "
                    "measure/date pairs; request a narrower date range"
                ) from None
    observations = read_demanded(conn, pairs, definitions)
    evaluation = Evaluation(definitions, observations, now=instant)
    projections = {}
    for chart in formula_charts:
        first, last, today = bounds[chart["id"]]
        definition = bound_definitions[chart["measure_id"]]
        points = []
        for day in days_by_chart[chart["id"]]:
            pair = MeasureDate(definition.id, day)
            if day > today and pair not in observations:
                actual = MeasureValue(
                    definition.id,
                    day,
                    definition.definition_revision,
                    MeasureReadStatus.missing_input,
                    None,
                    (),
                    (pair,),
                )
            else:
                actual = evaluation.measure(pair)
            expected = evaluation.expression(chart["expectation"], day, definition.timezone)
            points.append(
                projection_point(
                    day,
                    actual,
                    expected,
                    chart.get("direction", "atleast"),
                    phase_changed(chart["expectation"], day),
                )
            )
        projections[chart["id"]] = scalar_projection(first, last, today, points)
    from planner.measure_sources.data import read_health

    health = read_health(
        conn,
        tuple(
            sorted({item.source_id for item in definitions.values() if item.source_id is not None})
        ),
        now=instant,
    )
    return projections, {item.source_id: asdict(item) for item in health}


def _projection_json(projection: ScalarChartProjection) -> JsonDict:
    return {
        **projection,
        "points": [
            {**point, "actual": asdict(point["actual"]), "expected": asdict(point["expected"])}
            for point in projection["points"]
        ],
    }


def readings_json(
    conn: sqlite3.Connection,
    goal_id: str,
    now: datetime,
    *,
    first_date: str | None = None,
    last_date: str | None = None,
) -> JsonDict:
    with transaction(conn, read_only=True):
        goal = data.read_goal(conn, goal_id)
        today, midnight = calendar_projection(now)
        identifiers = measure_ids(goal.primary_chart, goal.page)
        _, midnight = reading_calendar(conn, goal, now)
        projections, sources = _formula_projections(conn, goal, now, first_date, last_date)
        values: list[JsonDict] = []
        primary_observations: list[tuple[str, float | None]] = []
        for identifier in identifiers:
            definition = read_measure(conn, identifier)
            last = last_date or now.astimezone(ZoneInfo(definition.timezone)).date().isoformat()
            if first_date is not None:
                first = first_date
            else:
                earliest = _earliest_date(conn, identifier)
                first = min(earliest, last) if earliest else last
            reading = read_values(
                conn,
                ReadMeasuresBody(
                    measure_ids=[identifier],
                    first_date=first,
                    last_date=last,
                ),
                now=int(now.timestamp()),
            )
            values.extend(asdict(value) for value in reading.values)
            sources.update({source.source_id: asdict(source) for source in reading.sources})
            if goal.primary_chart and goal.primary_chart.get("measure_id") == identifier:
                primary_observations = [(value.date, value.value) for value in reading.values]
        if goal.kind == "binary":
            status = goal_status(goal.completed, goal.deadline, today)
        elif goal.primary_chart and goal.primary_chart["id"] in projections:
            status = projection_status(goal.primary_chart, projections[goal.primary_chart["id"]])
        else:
            status = chart_status(goal.primary_chart, primary_observations)
        return dict(
            goal_id=goal.id,
            revision=goal.revision,
            status=status,
            status_date=today,
            next_refresh_at=midnight,
            values=values,
            sources=list(sources.values()),
            charts={
                identifier: _projection_json(projection)
                for identifier, projection in projections.items()
            },
        )
