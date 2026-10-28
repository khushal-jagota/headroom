"""Pure status and calendar projections; completion never changes membership."""

from datetime import date, datetime, time, timedelta

from planner.goals.contracts import ChartBlock, Tone


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
