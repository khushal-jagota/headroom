"""Pure status and calendar projections; completion never changes membership."""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta

from planner.goals.contracts import ChartBlock, Tone
from planner.measures.contracts import MeasureValue, ScalarCoverageStatus


def goal_status(completed: bool, deadline: str | None, today: str) -> Tone:
    if completed:
        return "good"
    if deadline is None or today < deadline:
        return "neutral"
    return "attention" if today == deadline else "bad"


def calendar_projection(now: datetime) -> tuple[str, int]:
    tomorrow = now.date() + timedelta(days=1)
    midnight = datetime.combine(tomorrow, time.min, tzinfo=now.tzinfo)
    return now.date().isoformat(), int(midnight.timestamp())


def quantitative_status(value: float | None, target: float | None, direction: str) -> Tone:
    if value is None or target is None:
        return "neutral"
    reached = value <= target if direction == "atmost" else value >= target
    return "good" if reached else "bad"


def chart_target(chart: ChartBlock, reading_date: str) -> float | None:
    """Use the authored reference at the reading date, as the shared chart does."""
    references = sorted(chart.get("reference", []), key=lambda point: point["date"])
    if not references:
        return chart.get("target")
    for point in references:
        if point["date"] == reading_date:
            return point["value"]
    before = [point for point in references if point["date"] < reading_date]
    after = [point for point in references if point["date"] > reading_date]
    if not before or not after:
        return None
    left, right = before[-1], after[0]
    try:
        elapsed = (date.fromisoformat(reading_date) - date.fromisoformat(left["date"])).days
    except ValueError:
        return None
    duration = (date.fromisoformat(right["date"]) - date.fromisoformat(left["date"])).days
    return left["value"] + (right["value"] - left["value"]) * elapsed / duration


def chart_summary(
    chart: ChartBlock, observations: Sequence[MeasureValue | tuple[str, float | None]]
) -> tuple[float | None, float | None]:
    """The overview's all-time reading, independent of local chart range/inspection."""
    selected_range = next(
        (group for group in chart.get("ranges", []) if group["id"] == "all"),
        None if "series" in chart else next(iter(chart.get("ranges", [])), None),
    )
    series = selected_range["series"] if selected_range is not None else chart.get("series", [])
    if selected_range is not None and "target" in selected_range:
        chart = {**chart, "target": selected_range["target"]}
    points: dict[str, dict[str, float | None]] = {}
    categories = ["actual"]
    if chart.get("measure_id"):
        points = {
            label: {"actual": value}
            for label, value in sorted(
                (item.date, item.value) if isinstance(item, MeasureValue) else item
                for item in observations
            )
        }
    else:
        categories = ["actual" if index == 0 else item["name"] for index, item in enumerate(series)]
        for index, item in enumerate(series):
            for point in item["points"]:
                points.setdefault(point["label"], {})[categories[index]] = point["value"]
    if not points:
        return None, chart.get("target")
    for label, values in points.items():
        if chart.get("reference") or "target" in chart:
            values["target"] = chart_target(chart, label)
    latest = next(reversed(points.values()))
    if chart.get("stacked") or chart["kind"] == "allocation":
        parts = [latest.get(key) for key in categories if key != "target"]
        value = (
            sum(part for part in parts if part is not None) if parts and None not in parts else None
        )
        return value, latest.get("target")
    if chart.get("aggregate") == "sum":
        actuals = [point["actual"] for point in points.values() if point.get("actual") is not None]
        targets = [point["target"] for point in points.values() if point.get("target") is not None]
        return (
            sum(value for value in actuals if value is not None) if actuals else None,
            sum(value for value in targets if value is not None) if targets else None,
        )
    value = latest.get("actual")
    if chart["kind"] in ("progress", "time-budget") and value is not None and value < 0:
        value = None
    return value, latest.get("target")


def chart_status(
    chart: ChartBlock | None, observations: Sequence[MeasureValue | tuple[str, float | None]]
) -> Tone:
    if chart is None:
        return "neutral"
    if chart.get("measure_id"):
        readings = sorted(
            (item for item in observations if isinstance(item, MeasureValue)),
            key=lambda item: item.date,
        )
        contributors = readings if chart.get("aggregate") == "sum" else readings[-1:]
        if any(
            item.value is None
            or item.coverage_status in (ScalarCoverageStatus.partial, ScalarCoverageStatus.unknown)
            for item in contributors
        ):
            return "neutral"
    value, target = chart_summary(chart, observations)
    tone = quantitative_status(
        value,
        target,
        chart.get("direction", "atmost" if chart["kind"] == "time-budget" else "atleast"),
    )
    return chart.get("below_target_tone", "bad") if tone == "bad" else tone
