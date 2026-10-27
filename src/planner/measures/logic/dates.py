"""Calendar dates and complete periods, independent of persistence."""

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


def period_interval(end: str, period: CountPeriod) -> PeriodInterval:
    last = calendar_date(end)
    try:
        following = last + timedelta(days=1)
    except OverflowError:
        raise invalid("invalid period boundary") from None
    if period is CountPeriod.day:
        first = last
    elif period is CountPeriod.week:
        if last.weekday() != 6:
            raise invalid("date is not an ISO week end")
        first = last - timedelta(days=6)
    else:
        if following.day != 1:
            raise invalid("date is not a calendar month end")
        first = last.replace(day=1)
    return PeriodInterval(first.isoformat(), following.isoformat())


def complete_interval(end: str, period: CountPeriod, zone: str, now: int) -> PeriodInterval:
    interval = period_interval(end, period)
    if calendar_date(interval.end_date_exclusive) > local_date(now, zone):
        raise invalid("period is not complete")
    return interval


def latest_complete_date(now: int, zone: str, period: CountPeriod) -> str:
    today = local_date(now, zone)
    if period is CountPeriod.day:
        end = today - timedelta(days=1)
    elif period is CountPeriod.week:
        end = today - timedelta(days=today.weekday() + 1)
    else:
        end = today.replace(day=1) - timedelta(days=1)
    return end.isoformat()


def date_range(first: str, last: str, *, maximum: int) -> tuple[str, ...]:
    start, end = calendar_date(first), calendar_date(last)
    length = (end - start).days + 1
    if length < 1 or length > maximum:
        raise invalid("measure date range exceeds its limit")
    return tuple((start + timedelta(days=i)).isoformat() for i in range(length))


def offset_date(day: str, offset: int) -> str:
    try:
        return (calendar_date(day) + timedelta(days=offset)).isoformat()
    except OverflowError:
        raise invalid("measure date offset is out of range") from None
