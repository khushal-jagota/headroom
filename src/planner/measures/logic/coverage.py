"""Source coverage facts affect comparisons, never retained numeric values."""

from collections.abc import Iterable
from datetime import UTC, datetime, time

from planner.core.contracts import PlannerError
from planner.measures.contracts import Measure, MeasureKind, OutputMeasure, ScalarCoverageStatus
from planner.measures.logic.dates import calendar_date, invalid, period_interval, timezone
from planner.measures.logic.formulas import number


def validate_coverage(start: object, end: object, limitation: object) -> None:
    for value in (start, end):
        if value is not None:
            timestamp = number(value)
            try:
                datetime.fromtimestamp(timestamp, UTC)
            except (OverflowError, OSError, ValueError):
                raise invalid("scalar query timestamp is outside the UTC datetime range") from None
    if start is not None and end is not None and number(start) >= number(end):
        raise invalid("scalar query interval must be positive")
    if limitation is not None and (not isinstance(limitation, str) or not limitation.strip()):
        raise invalid("invalid scalar coverage limitation")


def combine_coverage(
    values: Iterable[tuple[ScalarCoverageStatus, tuple[str, ...]]],
) -> tuple[ScalarCoverageStatus, tuple[str, ...]]:
    priorities = {
        ScalarCoverageStatus.not_applicable: 0,
        ScalarCoverageStatus.complete: 1,
        ScalarCoverageStatus.unknown: 2,
        ScalarCoverageStatus.partial: 3,
    }
    status = ScalarCoverageStatus.not_applicable
    reasons: dict[str, None] = {}
    for current, current_reasons in values:
        if priorities[current] > priorities[status]:
            status = current
        reasons.update(dict.fromkeys(current_reasons))
    return status, tuple(reasons)


def observation_coverage(
    measure: Measure,
    captured: OutputMeasure | None,
    day: str,
    start: float | None,
    end: float | None,
    limitation: str | None,
) -> tuple[ScalarCoverageStatus, tuple[str, ...]]:
    reasons = [limitation] if limitation is not None else []
    period_sensitive = measure.kind is MeasureKind.period_count or (
        captured is not None and captured.kind is MeasureKind.period_count
    )
    if not period_sensitive:
        return (
            (ScalarCoverageStatus.partial, tuple(reasons))
            if reasons
            else (ScalarCoverageStatus.not_applicable, ())
        )
    status = ScalarCoverageStatus.unknown
    if captured is None or captured.kind is not MeasureKind.period_count or captured.period is None:
        reasons.append("Period coverage is unavailable for this observation.")
    else:
        try:
            interval = period_interval(day, captured.period)
            zone = timezone(captured.timezone)
            expected_start = datetime.combine(
                calendar_date(interval.start_date), time.min, zone
            ).timestamp()
            expected_end = datetime.combine(
                calendar_date(interval.end_date_exclusive), time.min, zone
            ).timestamp()
        except (PlannerError, OverflowError, OSError, ValueError):
            reasons.append("The period boundaries are unavailable for this observation.")
        else:
            if (start is not None and start != expected_start) or (
                end is not None and end != expected_end
            ):
                status = ScalarCoverageStatus.partial
                reasons.append("The query interval does not match the complete local period.")
            elif start is not None and end is not None:
                status = ScalarCoverageStatus.complete
            else:
                reasons.append("Both query bounds are required to establish period coverage.")
        if (captured.kind, captured.timezone, captured.period) != (
            measure.kind,
            measure.timezone,
            measure.period,
        ):
            if status is not ScalarCoverageStatus.partial:
                status = ScalarCoverageStatus.unknown
            reasons.append(
                "The period definition differs from the captured observation definition."
            )
    if limitation is not None:
        status = ScalarCoverageStatus.partial
    return status, tuple(reasons)
