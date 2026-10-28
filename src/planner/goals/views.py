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
from planner.core.contracts import JsonDict
from planner.goals import data
from planner.goals.contracts import Goal, GoalResponse, GoalsResponse
from planner.goals.logic.documents import measure_ids
from planner.goals.logic.status import (
    calendar_projection,
    chart_status,
    goal_status,
)
from planner.measures.actions import read_values
from planner.measures.contracts import ReadMeasuresBody
from planner.measures.data import read_measure, transaction


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
    for identifier in measure_ids(goal.primary_chart, goal.page):
        definition = read_measure(conn, identifier)
        local_day, next_day = calendar_projection(now.astimezone(ZoneInfo(definition.timezone)))
        dates.append(identifier + ":" + local_day)
        midnight = min(midnight, next_day)
    return "|".join(dates), midnight


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
        values: list[JsonDict] = []
        sources = {}
        primary_observations: list[tuple[str, float | None]] = []
        for identifier in identifiers:
            definition = read_measure(conn, identifier)
            last = last_date or now.astimezone(ZoneInfo(definition.timezone)).date().isoformat()
            if first_date is not None:
                first = first_date
            else:
                earliest = conn.execute(
                    "WITH RECURSIVE inputs(id) AS (SELECT ? UNION "
                    "SELECT input_measure_id FROM measure_dependencies "
                    "JOIN inputs ON measure_id=id) "
                    "SELECT MIN(date) FROM (SELECT date FROM measure_observations "
                    "WHERE measure_id IN inputs "
                    "UNION ALL SELECT date FROM measure_history_gaps WHERE measure_id IN inputs)",
                    (identifier,),
                ).fetchone()[0]
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
        )
