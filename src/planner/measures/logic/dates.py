"""Date identity and explicit arithmetic windows, independent of measure kinds."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from planner.core.contracts import ErrorCode, PlannerError
from planner.measures.contracts import CountPeriod, PeriodInterval


def invalid(message: str) -> PlannerError:
    return PlannerError(ErrorCode.validation, message)


def calendar_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise invalid("invalid measure date") from None
    if parsed.isoformat() != value:
        raise invalid("measure date must use YYYY-MM-DD")
    return parsed


def timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise invalid("invalid measure timezone") from None


def local_date(now: int, zone: str) -> date:
    try:
        return datetime.fromtimestamp(now, UTC).astimezone(timezone(zone)).date()
    except (OverflowError, OSError, ValueError):
        raise invalid("invalid measure instant") from None


def period_interval(day: str, period: CountPeriod) -> PeriodInterval:
    """The explicit day, ISO week or calendar month containing this date."""
    current = calendar_date(day)
    try:
        if period is CountPeriod.day:
            first, following = current, current + timedelta(days=1)
        elif period is CountPeriod.week:
            first = current - timedelta(days=current.weekday())
            following = first + timedelta(days=7)
        else:
            first = current.replace(day=1)
            following = (first + timedelta(days=31)).replace(day=1)
    except (OverflowError, ValueError):
        raise invalid("invalid period boundary") from None
    return PeriodInterval(first.isoformat(), following.isoformat())


def date_range(first: str, last: str) -> tuple[str, ...]:
    start, end = calendar_date(first), calendar_date(last)
    length = (end - start).days + 1
    if length < 1:
        raise invalid("measure date range is reversed")
    return tuple((start + timedelta(days=i)).isoformat() for i in range(length))


def offset_date(day: str, offset: int) -> str:
    try:
        return (calendar_date(day) + timedelta(days=offset)).isoformat()
    except OverflowError:
        raise invalid("measure date offset is out of range") from None
