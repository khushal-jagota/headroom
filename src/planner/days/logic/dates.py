"""Planning-date math (§6.1). The calendar date of (now − boundary_hour hours).
Matches the ``PlanningDateFn`` contract named in ``days/contracts.py``. Stdlib
only."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def planning_date(now: datetime, boundary_hour: int) -> date:
    """§6.1: planning date = calendar date of (now − boundary_hour hours).

    05:00 belongs to the NEW date (04:59 → previous, 05:00 → current): the shift
    falls straight out of the subtraction, so there is no ``>``/``>=`` branch to
    get wrong. Tz-agnostic — works on an aware local ``now`` or a naive test one.
    """
    return (now - timedelta(hours=boundary_hour)).date()
