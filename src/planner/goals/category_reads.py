"""Compose complete retained inputs for the pure category projections."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from planner.datasets import data as datasets
from planner.datasets.contracts import Dataset, DatasetsRead, ReadDatasetsBody
from planner.goals import configuration as limits
from planner.goals.contracts import CategoryChartProjection, ChartBlock, Goal
from planner.goals.logic.categories import (
    category_bounds,
    dataset_chart_projection,
    scalar_inputs_chart_projection,
)
from planner.goals.logic.documents import charts
from planner.measure_sources.data import read_health
from planner.measures.contracts import (
    Measure,
    MeasureDate,
    MeasureReadStatus,
    MeasureValue,
    SourceHealthSummary,
)
from planner.measures.data import (
    earliest_retained_date,
    read_definitions,
    read_demanded,
    read_recorded_dates,
)
from planner.measures.logic.dates import calendar_date, date_range, invalid
from planner.measures.logic.evaluation import Evaluation, demanded_pairs


def _plans(
    conn: sqlite3.Connection,
    goal: Goal,
    now: datetime,
    first_date: str | None,
    last_date: str | None,
) -> tuple[
    dict[str, Dataset],
    dict[str, Measure],
    list[tuple[ChartBlock, str | None, str, str]],
]:
    bound = [chart for chart in charts(goal.primary_chart, goal.page) if "binding" in chart]
    dataset_ids: set[str] = set()
    measure_ids: set[str] = set()
    for chart in bound:
        binding = chart["binding"]
        if binding["kind"] == "dataset":
            dataset_ids.add(binding["dataset_id"])
        else:
            measure_ids.update(item["measure_id"] for item in binding["inputs"])
    dataset_definitions = {
        identifier: datasets.read_dataset(conn, identifier) for identifier in sorted(dataset_ids)
    }
    measure_definitions = read_definitions(conn, tuple(sorted(measure_ids)))
    plans = []
    projected_dates = 0
    for chart in bound:
        binding = chart["binding"]
        if binding["kind"] == "dataset":
            definition = dataset_definitions[binding["dataset_id"]]
            zone = definition.timezone
            earliest = datasets.earliest_retained_date(conn, definition.id)
        else:
            zone = measure_definitions[binding["inputs"][0]["measure_id"]].timezone
            retained = [
                day
                for item in binding["inputs"]
                if (day := earliest_retained_date(conn, item["measure_id"])) is not None
            ]
            earliest = min(retained) if retained else None
        today = now.astimezone(ZoneInfo(zone)).date().isoformat()
        bounds = category_bounds(today, earliest, first_date, last_date)
        first = min(item[1] for item in bounds)
        last = max(item[2] for item in bounds)
        projected_dates += (calendar_date(last) - calendar_date(first)).days + 1
        if projected_dates > limits.MAX_PROJECTED_CHART_DATES:
            raise invalid(
                f"goal projections exceed {limits.MAX_PROJECTED_CHART_DATES} chart-date points; "
                "request a narrower date range"
            )
        plans.append((chart, earliest, first, last))
    return dataset_definitions, measure_definitions, plans


def _measure_ranges(
    plans: list[tuple[ChartBlock, str | None, str, str]],
) -> list[tuple[str, str, str]]:
    bounds: dict[str, tuple[str, str]] = {}
    for chart, _, first, last in plans:
        binding = chart["binding"]
        if binding["kind"] != "scalar_inputs":
            continue
        for item in binding["inputs"]:
            identifier = item["measure_id"]
            old = bounds.get(identifier, (first, last))
            bounds[identifier] = min(first, old[0]), max(last, old[1])
    return [(identifier, first, last) for identifier, (first, last) in sorted(bounds.items())]


def category_read_ranges(
    conn: sqlite3.Connection,
    goal: Goal,
    now: datetime,
    *,
    first_date: str | None = None,
    last_date: str | None = None,
) -> tuple[list[tuple[str, str, str]], int]:
    """Include category inputs in the existing shared formula response budget."""
    _, _, plans = _plans(conn, goal, now, first_date, last_date)
    return _measure_ranges(plans), sum(
        (calendar_date(last) - calendar_date(first)).days + 1 for _, _, first, last in plans
    )


def _scalar_values(
    conn: sqlite3.Connection,
    definitions: dict[str, Measure],
    ranges: list[tuple[str, str, str]],
    *,
    now: int,
) -> tuple[list[MeasureValue], dict[str, frozenset[str]]]:
    count = sum((calendar_date(last) - calendar_date(first)).days + 1 for _, first, last in ranges)
    if count > limits.MAX_DEMANDED_MEASURE_DATES:
        raise invalid(
            f"goal projection exceeds {limits.MAX_DEMANDED_MEASURE_DATES} demanded "
            "measure/date pairs; request a narrower date range"
        )
    roots = tuple(
        MeasureDate(identifier, day)
        for identifier, first, last in ranges
        for day in date_range(first, last)
    )
    try:
        pairs = demanded_pairs(
            roots, definitions, now=now, limit=limits.MAX_DEMANDED_MEASURE_DATES, missing_ok=True
        )
    except RecursionError:
        pairs = set()
        for root in roots:
            try:
                pairs.update(
                    demanded_pairs(
                        (root,),
                        definitions,
                        now=now,
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
    evaluation = Evaluation(definitions, observations, now=now)
    submitted = {
        identifier: read_recorded_dates(conn, identifier, first, last)
        for identifier, first, last in ranges
    }
    values = []
    instant = datetime.fromtimestamp(now, ZoneInfo("UTC"))
    for pair in roots:
        definition = definitions[pair.measure_id]
        today = instant.astimezone(ZoneInfo(definition.timezone)).date().isoformat()
        if pair.date > today and pair.date not in submitted[pair.measure_id]:
            values.append(
                MeasureValue(
                    definition.id,
                    pair.date,
                    definition.definition_revision,
                    MeasureReadStatus.missing_input,
                    None,
                    (),
                    (pair,),
                )
            )
        else:
            values.append(evaluation.measure(pair))
    return values, submitted


def category_projections(
    conn: sqlite3.Connection,
    goal: Goal,
    now: datetime,
    *,
    first_date: str | None = None,
    last_date: str | None = None,
) -> tuple[dict[str, CategoryChartProjection], list[SourceHealthSummary]]:
    """Compose all bound charts inside the caller's read transaction."""
    _, definitions, plans = _plans(conn, goal, now, first_date, last_date)
    if not plans:
        return {}, []
    instant = int(now.timestamp())
    values, submitted = _scalar_values(conn, definitions, _measure_ranges(plans), now=instant)
    dataset_ranges: dict[str, tuple[str, str]] = {}
    for chart, _, first, last in plans:
        binding = chart["binding"]
        if binding["kind"] == "dataset":
            identifier = binding["dataset_id"]
            old = dataset_ranges.get(identifier, (first, last))
            dataset_ranges[identifier] = min(first, old[0]), max(last, old[1])
    retained: dict[str, DatasetsRead] = {}
    health = {
        item.source_id: item
        for item in read_health(
            conn,
            tuple(
                sorted(
                    {item.source_id for item in definitions.values() if item.source_id is not None}
                )
            ),
            now=instant,
        )
    }
    for identifier, (first, last) in dataset_ranges.items():
        reading = datasets.read_values(
            conn,
            ReadDatasetsBody(dataset_ids=[identifier], first_date=first, last_date=last),
            now=instant,
        )
        retained[identifier] = reading
        health.update((item.source_id, item) for item in reading.sources)
    projections = {}
    for chart, earliest, _, _ in plans:
        binding = chart["binding"]
        if binding["kind"] == "dataset":
            projection = dataset_chart_projection(
                chart,
                retained[binding["dataset_id"]],
                now=instant,
                earliest_date=earliest,
                first_date=first_date,
                last_date=last_date,
            )
        else:
            identifiers = {item["measure_id"] for item in binding["inputs"]}
            projection = scalar_inputs_chart_projection(
                chart,
                definitions,
                [value for value in values if value.measure_id in identifiers],
                now=instant,
                earliest_date=earliest,
                submitted_dates=submitted,
                first_date=first_date,
                last_date=last_date,
            )
        projections[chart["id"]] = projection
    return projections, list(health.values())
