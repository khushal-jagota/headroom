"""Planning-date math (§6.1). The calendar date of (now − boundary_hour hours).
Matches the ``PlanningDateFn`` contract named in ``days/contracts.py``. Stdlib
only, plus the leaf ``core.ids`` / ``core.errors`` for the shared day-id resolver."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from planner.core import ids
from planner.core.errors import ErrorCode, PlannerError


def planning_date(now: datetime, boundary_hour: int) -> date:
    """§6.1: planning date = calendar date of (now − boundary_hour hours).

    05:00 belongs to the NEW date (04:59 → previous, 05:00 → current): the shift
    falls straight out of the subtraction, so there is no ``>``/``>=`` branch to
    get wrong. Tz-agnostic — works on an aware local ``now`` or a naive test one.
    """
    return (now - timedelta(hours=boundary_hour)).date()


def resolve_day_id(day_seg: str, now: datetime, boundary_hour: int) -> str:
    """A day path/param segment → a canonical ``day_YYYY-MM-DD`` id. ``today`` resolves
    through the planning date; any other value is parsed as an ISO date then re-formatted
    via ``ids.day_id`` (so compact forms like ``20260704`` still canonicalise). An
    unparseable segment raises the validation envelope. Pure: the one home both the day
    routes and the ticket ``--day`` filter share, so neither re-implements the rule."""
    if day_seg == "today":
        return ids.day_id(planning_date(now, boundary_hour))
    try:
        parsed = date.fromisoformat(day_seg)
    except ValueError:
        raise PlannerError(ErrorCode.validation, "invalid date", {"date": day_seg}) from None
    return ids.day_id(parsed)
