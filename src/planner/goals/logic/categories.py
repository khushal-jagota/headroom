"""Pure binding rules and retained category projections, without storage access."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta
from typing import Literal, cast

from planner.datasets.contracts import (
    Dataset,
    DatasetFieldKind,
    DatasetResult,
    DatasetRow,
    DatasetsRead,
)
from planner.goals import configuration as limits
from planner.goals.contracts import (
    CategoryChartPoint,
    CategoryChartProjection,
    CategoryChartRange,
    CategoryChartSeries,
    CategoryChartSummary,
    CategoryChartValue,
    CategoryPointSummary,
    ChartBlock,
    DatasetChartBinding,
    DatasetFieldMetric,
    ScalarInputsChartBinding,
)
from planner.measures.contracts import (
    Measure,
    MeasureOrigin,
    MeasureReadStatus,
    MeasureValue,
    ScalarCoverageStatus,
)
from planner.measures.logic.coverage import combine_coverage
from planner.measures.logic.dates import calendar_date, date_range, invalid, local_date, timezone


def _text(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must be nonempty")


def validate_category_chart(chart: ChartBlock) -> None:
    """Check a bound chart's combinations, roles, keys, and geometry."""
    if "binding" not in chart:
        return
    binding = chart["binding"]
    if any(key in chart for key in ("measure_id", "expectation", "manual", "series", "ranges")):
        raise ValueError("a chart binding cannot combine other live or authored inputs")
    if "aggregate" in chart:
        raise ValueError("a bound chart selects aggregation inside its binding")
    if chart["kind"] == "binary":
        raise ValueError("binary charts do not accept numeric bindings")
    if binding["aggregation"] not in ("sum", "latest"):
        raise ValueError("unknown binding aggregation")
    if binding["kind"] == "dataset":
        _text(binding["dataset_id"], "dataset ID")
        _text(binding["group_by"], "group-by dimension")
        dimensions = [item["dimension"] for item in binding["filters"]]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("dataset filter dimensions must be unique")
        for dimension in dimensions:
            _text(dimension, "filter dimension")
        metric = binding["metric"]
        if "field" in metric:
            _text(cast(DatasetFieldMetric, metric)["field"], "metric field")
        else:
            _text(metric["numerator"], "numerator field")
            _text(metric["denominator"], "denominator field")
            if not math.isfinite(metric.get("scale", 1.0)):
                raise ValueError("ratio scale must be finite")
            if chart.get("stacked") or chart["kind"] not in ("bar", "line", "number", "gauge"):
                raise ValueError("ratio metrics require unstacked bars, lines, or scalar readouts")
        if chart["kind"] == "time-budget":
            raise ValueError("a live time budget requires named spent and planned inputs")
    else:
        inputs = binding["inputs"]
        keys = [item["key"] for item in inputs]
        if not inputs or len(keys) != len(set(keys)):
            raise ValueError("named scalar input keys must be nonempty and unique")
        for item in inputs:
            _text(item["key"], "input key")
            _text(item["label"], "input label")
            _text(item["measure_id"], "input measure ID")
        roles = [item.get("role") for item in inputs]
        if chart["kind"] == "time-budget":
            if len(inputs) != 2 or set(roles) != {"spent", "planned"}:
                raise ValueError("time budgets require one spent and one planned input")
        elif any(role is not None for role in roles):
            raise ValueError("spent and planned roles belong to time-budget charts")


def validate_category_references(
    chart: ChartBlock,
    *,
    datasets: Mapping[str, Dataset],
    measures: Mapping[str, Measure],
) -> None:
    """Check referenced names against supplied current definitions."""
    if "binding" not in chart:
        return
    validate_category_chart(chart)
    binding = chart["binding"]
    if binding["kind"] == "scalar_inputs":
        if any(item["measure_id"] not in measures for item in binding["inputs"]):
            raise ValueError("bound input measure does not exist")
        return
    definition = datasets.get(binding["dataset_id"])
    if definition is None:
        raise ValueError("bound dataset does not exist")
    dimensions = {item.name for item in definition.dimensions}
    required = {binding["group_by"], *(item["dimension"] for item in binding["filters"])}
    if required - dimensions:
        raise ValueError("binding references an unknown dataset dimension")
    if set(_fields(binding)) - {item.name for item in definition.fields}:
        raise ValueError("binding references an unknown dataset field")


def category_bounds(
    today: str,
    earliest_date: str | None,
    first_date: str | None,
    last_date: str | None,
) -> list[tuple[Literal["week", "month", "all"], str, str]]:
    """Return ordered range ID, first date, and last date tuples."""
    calendar_date(today)
    anchor = calendar_date(last_date if last_date is not None else today)
    lower = calendar_date(first_date) if first_date is not None else None
    earliest = calendar_date(earliest_date) if earliest_date is not None else anchor
    if lower is not None and lower > anchor:
        raise invalid("goal date range is reversed")
    result: list[tuple[Literal["week", "month", "all"], str, str]] = []
    windows: tuple[tuple[Literal["week", "month", "all"], int | None], ...] = (
        ("week", 7),
        ("month", 30),
        ("all", None),
    )
    for key, length in windows:
        if length is None:
            first = lower if lower is not None else min(earliest, anchor)
        else:
            first = date.fromordinal(max(1, anchor.toordinal() - length + 1))
            if lower is not None:
                first = max(first, lower)
        if (anchor - first).days + 1 > limits.MAX_SCALAR_PROJECTION_DATES:
            raise invalid(
                f"goal projection exceeds {limits.MAX_SCALAR_PROJECTION_DATES} dates per chart; "
                "request a narrower date range"
            )
        result.append((key, first.isoformat(), anchor.isoformat()))
    return result


def _key(family: str, key: str) -> str:
    return family + ":" + json.dumps(key, ensure_ascii=False, separators=(",", ":"))


def _fields(binding: DatasetChartBinding) -> tuple[str, ...]:
    metric = binding["metric"]
    return (
        (cast(DatasetFieldMetric, metric)["field"],)
        if "field" in metric
        else (metric["numerator"], metric["denominator"])
    )


def _value(
    value: float | None,
    *,
    status: MeasureReadStatus = MeasureReadStatus.value,
    numerator: float | None = None,
    denominator: float | None = None,
    partial: bool = False,
    missing_dates: Sequence[str] = (),
    reasons: Sequence[str] = (),
    coverage_status: ScalarCoverageStatus | None = None,
) -> CategoryChartValue:
    if value is not None and not math.isfinite(value):
        value, status = None, MeasureReadStatus.arithmetic_limit
        reasons = [*reasons, "The calculated value exceeds the numeric limit."]
    result: CategoryChartValue = {
        "status": status,
        "value": value,
        "numerator": numerator,
        "denominator": denominator,
        "partial": partial,
        "missing_dates": sorted(set(missing_dates)),
        "reasons": list(dict.fromkeys(reasons)),
    }
    if coverage_status is not None:
        result["coverage_status"] = coverage_status
    return result


def _missing(reason: str, *, day: str | None = None) -> CategoryChartValue:
    return _value(
        None,
        status=MeasureReadStatus.missing_input,
        missing_dates=[] if day is None else [day],
        reasons=[reason],
    )


def _sum(values: Sequence[float]) -> float | None:
    try:
        result = math.fsum(values)
    except OverflowError:
        return None
    return result if math.isfinite(result) else None


def _combine(
    values: Sequence[CategoryChartValue],
    *,
    ratio: bool = False,
    scale: float = 1.0,
    require_all: bool = False,
) -> CategoryChartValue:
    usable = [
        item
        for item in values
        if (
            item["numerator"] is not None and item["denominator"] is not None
            if ratio
            else item["status"] == MeasureReadStatus.value and item["value"] is not None
        )
    ]
    missing = [day for item in values for day in item["missing_dates"]]
    reasons = [
        reason
        for item in values
        for reason in item["reasons"]
        if reason != "The denominator is zero."
    ]
    partial = any(item["partial"] for item in values) or len(usable) != len(values)
    coverage_status = (
        combine_coverage(
            (item.get("coverage_status", ScalarCoverageStatus.not_applicable), ())
            for item in values
        )[0]
        if any("coverage_status" in item for item in values)
        else None
    )
    if not usable or (require_all and len(usable) != len(values)):
        failed = next(
            (item["status"] for item in values if item["status"] != MeasureReadStatus.value),
            MeasureReadStatus.missing_input,
        )
        return _value(
            None,
            status=failed,
            partial=partial and bool(usable),
            missing_dates=missing,
            reasons=reasons or ["No usable inputs exist in this range."],
            coverage_status=coverage_status,
        )
    numerator = denominator = None
    if ratio:
        numerator = _sum([item["numerator"] for item in usable if item["numerator"] is not None])
        denominator = _sum(
            [item["denominator"] for item in usable if item["denominator"] is not None]
        )
        if numerator is not None and denominator == 0:
            return _value(
                None,
                status=MeasureReadStatus.division_by_zero,
                numerator=numerator,
                denominator=denominator,
                partial=partial,
                missing_dates=missing,
                reasons=[*reasons, "The denominator is zero."],
                coverage_status=coverage_status,
            )
        value = (
            numerator / denominator * scale
            if numerator is not None and denominator is not None
            else None
        )
    else:
        value = _sum([item["value"] for item in usable if item["value"] is not None])
    return _value(
        value,
        status=MeasureReadStatus.value if value is not None else MeasureReadStatus.arithmetic_limit,
        numerator=numerator,
        denominator=denominator,
        partial=partial,
        missing_dates=missing,
        reasons=reasons if value is not None else [*reasons, "The sum exceeds the numeric limit."],
        coverage_status=coverage_status,
    )


def _quantity(chart: ChartBlock, actual: CategoryChartValue) -> CategoryChartValue:
    if (
        chart["kind"] in ("allocation", "time-budget", "progress")
        and actual["value"] is not None
        and actual["value"] < 0
    ):
        return {
            **actual,
            "status": MeasureReadStatus.invalid_value,
            "value": None,
            "reasons": [*actual["reasons"], "This chart requires a nonnegative quantity."],
        }
    return actual


def _reference(chart: ChartBlock, day: str | None) -> CategoryChartValue | None:
    references = sorted(chart.get("reference", []), key=lambda item: item["date"])
    if references:
        if day is None:
            return None
        exact = next((item for item in references if item["date"] == day), None)
        if exact is not None:
            return _value(exact["value"])
        before = next((item for item in reversed(references) if item["date"] < day), None)
        after = next((item for item in references if item["date"] > day), None)
        if before is None or after is None:
            return _missing(f"No reference covers {day}.")
        first, last = calendar_date(before["date"]), calendar_date(after["date"])
        fraction = (calendar_date(day) - first).days / (last - first).days
        return _value(before["value"] + (after["value"] - before["value"]) * fraction)
    return _value(chart["target"]) if "target" in chart else None


def _summary(
    chart: ChartBlock,
    actual: CategoryChartValue,
    day: str | None,
    comparison: CategoryChartValue | None = None,
) -> CategoryChartSummary:
    if comparison is None:
        comparison = _reference(chart, day)
    if (
        chart["kind"] == "time-budget"
        and comparison is not None
        and comparison["value"] is not None
        and comparison["value"] <= 0
    ):
        comparison = {
            **comparison,
            "value": None,
            "status": MeasureReadStatus.invalid_value,
            "reasons": [*comparison["reasons"], "The planned quantity must be positive."],
        }
    difference = None
    meets = None
    if (
        actual["status"] == MeasureReadStatus.value
        and actual["value"] is not None
        and comparison is not None
        and comparison["status"] == MeasureReadStatus.value
        and comparison["value"] is not None
    ):
        delta = actual["value"] - comparison["value"]
        difference = delta if math.isfinite(delta) else None
        if difference is not None and all(
            not item["partial"]
            and item.get("coverage_status")
            not in (ScalarCoverageStatus.partial, ScalarCoverageStatus.unknown)
            for item in (actual, comparison)
        ):
            direction = chart.get(
                "direction", "atmost" if chart["kind"] == "time-budget" else "atleast"
            )
            meets = (
                actual["value"] <= comparison["value"]
                if direction == "atmost"
                else actual["value"] >= comparison["value"]
            )
    return {
        "actual": actual,
        "comparison": comparison,
        "difference": difference,
        "meets_expectation": meets,
        "date": day,
    }


def _coverage(result: DatasetResult, *, snapshot: bool) -> tuple[bool, list[str]]:
    reasons = [result.limitation] if result.limitation is not None else []
    if not snapshot:
        current = calendar_date(result.date)
        zone = timezone(result.output.timezone)
        start = datetime.combine(current, time.min, zone).timestamp()
        # date.max cannot have a following calendar day in Python.
        end = (
            datetime.combine(current + timedelta(days=1), time.min, zone).timestamp()
            if current < date.max
            else float("inf")
        )
        if result.query_end_at < end or (
            result.query_start_at is not None and result.query_start_at > start
        ):
            reasons.append(f"The query covers only part of {result.date}.")
        if result.query_end_at > end or (
            result.query_start_at is not None and result.query_start_at < start
        ):
            reasons.append(f"The query interval extends outside {result.date}.")
    return bool(reasons), reasons


def _groups(result: DatasetResult, binding: DatasetChartBinding) -> dict[str, list[DatasetRow]]:
    groups: dict[str, list[DatasetRow]] = {}
    for row in result.rows:
        if binding["group_by"] in row.dimensions and all(
            row.dimensions.get(item["dimension"]) in item["codes"] for item in binding["filters"]
        ):
            groups.setdefault(row.dimensions[binding["group_by"]], []).append(row)
    return groups


def _dataset_value(
    chart: ChartBlock,
    binding: DatasetChartBinding,
    result: DatasetResult | None,
    rows: Sequence[DatasetRow],
    day: str,
    *,
    category: bool = False,
) -> CategoryChartValue:
    if result is None:
        return _missing(f"No dataset result exists for {day}.", day=day)
    if result.unavailable_reason is not None:
        return _missing(f"{day}: {result.unavailable_reason}")
    dimensions = {item.name for item in result.output.dimensions}
    fields = {item.name: item for item in result.output.fields}
    required = {binding["group_by"], *(item["dimension"] for item in binding["filters"])}
    absent = sorted((required - dimensions) | (set(_fields(binding)) - set(fields)))
    if absent:
        return _missing(f"The retained declaration for {day} lacks: {', '.join(absent)}.")
    snapshot = any(fields[name].kind == DatasetFieldKind.snapshot for name in _fields(binding))
    partial, reasons = _coverage(
        result,
        snapshot=all(fields[name].kind == DatasetFieldKind.snapshot for name in _fields(binding)),
    )
    if not rows and category and (partial or snapshot or binding["aggregation"] == "latest"):
        return _value(
            None,
            status=MeasureReadStatus.missing_input,
            partial=partial,
            reasons=[*reasons, f"The category has no usable value on {day}."],
        )
    if not rows and snapshot:
        return _value(
            None,
            status=MeasureReadStatus.missing_input,
            partial=partial,
            reasons=[*reasons, f"The snapshot for {day} is empty."],
        )
    required_fields = _fields(binding)
    if any(any(name not in row.values for name in required_fields) for row in rows):
        return _missing(f"A required numeric field is absent from a retained row on {day}.")
    metric = binding["metric"]
    if "field" in metric:
        field_name = cast(DatasetFieldMetric, metric)["field"]
        quantities = [_value(row.values[field_name]) for row in rows]
        actual = _combine(quantities) if quantities else _value(0.0)
    else:
        numerator = _sum([row.values[metric["numerator"]] for row in rows])
        denominator = _sum([row.values[metric["denominator"]] for row in rows])
        paired = _value(
            None,
            status=(
                MeasureReadStatus.value
                if numerator is not None and denominator is not None
                else MeasureReadStatus.arithmetic_limit
            ),
            numerator=numerator,
            denominator=denominator,
            reasons=(
                []
                if numerator is not None and denominator is not None
                else ["The sum exceeds the numeric limit."]
            ),
        )
        actual = _combine([paired], ratio=True, scale=metric.get("scale", 1.0))
    actual = {
        **actual,
        "partial": actual["partial"] or partial,
        "reasons": list(dict.fromkeys([*actual["reasons"], *reasons])),
    }
    return _quantity(chart, actual)


def _range(
    key: Literal["week", "month", "all"],
    first: str,
    last: str,
    series: list[CategoryChartSeries],
    summary: CategoryChartSummary,
    points: list[CategoryPointSummary],
    *,
    grouped: bool,
) -> CategoryChartRange:
    return {
        "id": key,
        "label": {"week": "Week", "month": "Month", "all": "All time"}[key],
        "first_date": first,
        "last_date": last,
        "axis": "category" if grouped else "date",
        "series": series,
        "summary": summary,
        "point_summaries": points,
    }


def dataset_chart_projection(
    chart: ChartBlock,
    reading: DatasetsRead,
    *,
    now: int,
    earliest_date: str | None,
    first_date: str | None = None,
    last_date: str | None = None,
) -> CategoryChartProjection:
    """Project complete retained results with captured declarations."""
    validate_category_chart(chart)
    binding = chart["binding"]
    if binding["kind"] != "dataset":
        raise ValueError("dataset projection requires a dataset binding")
    definition = next((item for item in reading.datasets if item.id == binding["dataset_id"]), None)
    if definition is None:
        raise invalid("dataset projection requires its current definition")
    if reading.truncated:
        raise invalid(
            "a goal projection requires a complete dataset read; request a narrower range"
        )
    today = local_date(now, definition.timezone).isoformat()
    bounds = category_bounds(today, earliest_date, first_date, last_date)
    if reading.first_date > min(item[1] for item in bounds) or reading.last_date < max(
        item[2] for item in bounds
    ):
        raise invalid("dataset read does not cover the complete projection interval")
    results = {item.date: item for item in reading.results if item.dataset_id == definition.id}
    grouped_rows = {day: _groups(item, binding) for day, item in results.items()}
    labels = {
        item.code: item.label
        for item in reading.labels
        if item.dataset_id == definition.id and item.dimension == binding["group_by"]
    }
    metric = binding["metric"]
    ratio = "field" not in metric
    scale = metric.get("scale", 1.0) if "field" not in metric else 1.0
    grouped = chart["kind"] == "bar" and not chart.get("stacked", False)
    ranges: list[CategoryChartRange] = []
    for key, first, last in bounds:
        days = date_range(first, last)
        selected = max((day for day in results if first <= day <= last), default=None)
        codes = sorted({code for day in days for code in grouped_rows.get(day, {})})
        day_totals = {
            day: _dataset_value(
                chart,
                binding,
                results.get(day),
                [row for rows in grouped_rows.get(day, {}).values() for row in rows],
                day,
            )
            for day in days
        }
        dated = {
            code: {
                day: _dataset_value(
                    chart,
                    binding,
                    results.get(day),
                    grouped_rows.get(day, {}).get(code, []),
                    day,
                    category=True,
                )
                for day in days
            }
            for code in codes
        }
        if binding["aggregation"] == "latest":
            total = (
                day_totals[selected]
                if selected is not None
                else _missing("No dataset snapshot exists in this range.")
            )
            members = set(grouped_rows.get(selected, {})) if selected is not None else set()
            totals = {
                code: dated[code][selected] if selected is not None and code in members else None
                for code in codes
            }
            summary_date = selected
        else:
            total = _combine(list(day_totals.values()), ratio=ratio, scale=scale)
            totals = {
                code: _combine(list(dated[code].values()), ratio=ratio, scale=scale)
                for code in codes
            }
            summary_date = None
        summary = _summary(chart, total, summary_date)
        series: list[CategoryChartSeries] = []
        inspections: list[CategoryPointSummary] = []
        if grouped:
            category_points: list[CategoryChartPoint] = []
            for code in codes:
                actual = totals[code]
                if actual is None:
                    continue
                point_key = _key("category", code)
                category_points.append(
                    {
                        "key": point_key,
                        "label": labels.get(code, code),
                        "date": None,
                        "actual": actual,
                    }
                )
                inspections.append(
                    {"key": point_key, "summary": _summary(chart, actual, summary_date)}
                )
            series.append(
                {
                    "key": "actual",
                    "label": chart.get("title", definition.name),
                    "points": category_points,
                    "summary": total,
                }
            )
        else:
            for code in codes:
                series.append(
                    {
                        "key": _key("category", code),
                        "label": labels.get(code, code),
                        "points": [
                            {"key": day, "label": day, "date": day, "actual": dated[code][day]}
                            for day in days
                        ],
                        "summary": totals[code],
                    }
                )
            inspections = [
                {"key": day, "summary": _summary(chart, day_totals[day], day)} for day in days
            ]
        ranges.append(_range(key, first, last, series, summary, inspections, grouped=grouped))
    return {
        "kind": "category",
        "today": today,
        "timezone": definition.timezone,
        "ranges": ranges,
        "dataset_results": [
            results[day]
            for day in sorted(results)
            if min(item[1] for item in bounds) <= day <= max(item[2] for item in bounds)
        ],
        "measure_values": [],
    }


def _scalar_value(
    chart: ChartBlock,
    value: MeasureValue | None,
    identifier: str,
    day: str,
    submitted: bool,
) -> CategoryChartValue:
    if value is None:
        return _missing(
            f"No value for {identifier} exists on {day}.", day=None if submitted else day
        )
    reasons = (
        []
        if value.status == MeasureReadStatus.value
        else [f"{identifier} on {day}: {value.status.value}."]
    )
    actual = _value(
        value.value,
        status=value.status,
        partial=value.coverage_status == ScalarCoverageStatus.partial,
        missing_dates=[day] if value.value is None and not submitted else [],
        reasons=[*reasons, *value.coverage_reasons],
        coverage_status=value.coverage_status,
    )
    return _quantity(chart, actual)


def _scalar_summary(
    chart: ChartBlock,
    binding: ScalarInputsChartBinding,
    values: Mapping[str, CategoryChartValue],
    day: str | None,
) -> CategoryChartSummary:
    if chart["kind"] == "time-budget":
        spent = next(item["key"] for item in binding["inputs"] if item.get("role") == "spent")
        planned = next(item["key"] for item in binding["inputs"] if item.get("role") == "planned")
        return _summary(chart, values[spent], day, values[planned])
    return _summary(
        chart,
        _combine(list(values.values()), require_all=binding["aggregation"] == "latest"),
        day,
    )


def scalar_inputs_chart_projection(
    chart: ChartBlock,
    definitions: Mapping[str, Measure],
    values: Sequence[MeasureValue],
    *,
    now: int,
    earliest_date: str | None,
    submitted_dates: Mapping[str, frozenset[str]],
    first_date: str | None = None,
    last_date: str | None = None,
) -> CategoryChartProjection:
    """Project ordinary scalar reads without individual latest fallback."""
    validate_category_chart(chart)
    binding = chart["binding"]
    if binding["kind"] != "scalar_inputs":
        raise ValueError("named scalar projection requires a scalar_inputs binding")
    validate_category_references(chart, datasets={}, measures=definitions)
    zone = definitions[binding["inputs"][0]["measure_id"]].timezone
    today = local_date(now, zone).isoformat()
    bounds = category_bounds(today, earliest_date, first_date, last_date)
    identifiers = {item["measure_id"] for item in binding["inputs"]}
    actuals = {
        (item.measure_id, item.date): item for item in values if item.measure_id in identifiers
    }
    candidates = {day for identifier in identifiers for day in submitted_dates.get(identifier, ())}
    for observed in values:
        if (
            observed.measure_id in identifiers
            and definitions[observed.measure_id].origin == MeasureOrigin.assembled
            and observed.status == MeasureReadStatus.value
            and observed.date
            <= local_date(now, definitions[observed.measure_id].timezone).isoformat()
        ):
            candidates.add(observed.date)
    grouped = chart["kind"] == "bar" and not chart.get("stacked", False)
    ranges: list[CategoryChartRange] = []
    for key, first, last in bounds:
        days = date_range(first, last)
        selected = max((day for day in candidates if first <= day <= last), default=None)
        dated = {
            item["key"]: {
                day: _scalar_value(
                    chart,
                    actuals.get((item["measure_id"], day)),
                    item["measure_id"],
                    day,
                    day in submitted_dates.get(item["measure_id"], ()),
                )
                for day in days
            }
            for item in binding["inputs"]
        }
        totals = {
            item["key"]: (
                dated[item["key"]][selected]
                if selected is not None
                else _missing(f"No snapshot for {item['label']} exists in this range.")
            )
            if binding["aggregation"] == "latest"
            else _combine(list(dated[item["key"]].values()))
            for item in binding["inputs"]
        }
        summary_date = selected if binding["aggregation"] == "latest" else None
        summary = _scalar_summary(chart, binding, totals, summary_date)
        series: list[CategoryChartSeries] = []
        inspections: list[CategoryPointSummary] = []
        if grouped:
            points: list[CategoryChartPoint] = []
            for item in binding["inputs"]:
                point_key = _key("input", item["key"])
                points.append(
                    {
                        "key": point_key,
                        "label": item["label"],
                        "date": None,
                        "actual": totals[item["key"]],
                    }
                )
                inspections.append(
                    {
                        "key": point_key,
                        "summary": _summary(chart, totals[item["key"]], summary_date),
                    }
                )
            series.append(
                {
                    "key": "actual",
                    "label": chart.get("title", "Reading"),
                    "points": points,
                    "summary": summary["actual"],
                }
            )
        else:
            for item in binding["inputs"]:
                series.append(
                    {
                        "key": _key("input", item["key"]),
                        "label": item["label"],
                        "points": [
                            {
                                "key": day,
                                "label": day,
                                "date": day,
                                "actual": dated[item["key"]][day],
                            }
                            for day in days
                        ],
                        "summary": totals[item["key"]],
                    }
                )
            inspections = [
                {
                    "key": day,
                    "summary": _scalar_summary(
                        chart,
                        binding,
                        {item["key"]: dated[item["key"]][day] for item in binding["inputs"]},
                        day,
                    ),
                }
                for day in days
            ]
        ranges.append(_range(key, first, last, series, summary, inspections, grouped=grouped))
    lower, upper = min(item[1] for item in bounds), max(item[2] for item in bounds)
    return {
        "kind": "category",
        "today": today,
        "timezone": zone,
        "ranges": ranges,
        "dataset_results": [],
        "measure_values": [
            item
            for item in values
            if item.measure_id in identifiers and lower <= item.date <= upper
        ],
    }
