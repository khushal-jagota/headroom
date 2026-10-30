"""Pure calendar bounds and exact comparisons for scalar expectations."""

import math
from datetime import date

from planner.goals import configuration as limits
from planner.goals.contracts import ChartBlock, ScalarChartPoint, ScalarChartProjection, Tone
from planner.measures.contracts import ExpressionValue, MeasureValue
from planner.measures.logic.dates import calendar_date, invalid


def projection_bounds(
    today: str,
    earliest: str | None,
    deadline: str | None,
    first_date: str | None,
    last_date: str | None,
) -> tuple[str, str, int]:
    if first_date is None:
        start_of_view = date.fromordinal(
            max(1, calendar_date(today).toordinal() - limits.DEFAULT_PROJECTION_DATES + 1)
        ).isoformat()
        first = min(earliest or start_of_view, start_of_view)
    else:
        first = first_date
    last = last_date if last_date is not None else max(today, deadline or today)
    length = (calendar_date(last) - calendar_date(first)).days + 1
    if length < 1:
        raise invalid("goal date range is reversed")
    if length > limits.MAX_SCALAR_PROJECTION_DATES:
        raise invalid(
            f"goal projection exceeds {limits.MAX_SCALAR_PROJECTION_DATES} dates per chart; "
            "request a narrower date range"
        )
    return first, last, length


def projection_point(
    day: str,
    actual: MeasureValue,
    expected: ExpressionValue,
    direction: str,
    break_before: bool,
) -> ScalarChartPoint:
    difference = None
    meets = None
    left, right = actual.value, expected.value
    if left is not None and right is not None and math.isfinite(left) and math.isfinite(right):
        delta = left - right
        difference = delta if math.isfinite(delta) else None
        meets = left <= right if direction == "atmost" else left >= right
    return {
        "date": day,
        "actual": actual,
        "expected": expected,
        "difference": difference,
        "meets_expectation": meets,
        "break_before": break_before,
    }


def scalar_projection(
    first: str, last: str, today: str, points: list[ScalarChartPoint]
) -> ScalarChartProjection:
    return {
        "kind": "scalar",
        "first_date": first,
        "last_date": last,
        "today": today,
        "expected_source": "expectation",
        "points": points,
        "summary_date": min(last, today) if first <= today else None,
    }


def projection_status(chart: ChartBlock, projection: ScalarChartProjection) -> Tone:
    point = next(
        (point for point in projection["points"] if point["date"] == projection["summary_date"]),
        None,
    )
    if point is None or point["meets_expectation"] is None:
        return "neutral"
    return "good" if point["meets_expectation"] else chart.get("below_target_tone", "bad")
