"""Pure clock-slot and cadence rules for scheduled Ticket creation."""

from __future__ import annotations

import re
from datetime import date, datetime

from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date
from planner.scheduled_tickets.contracts import ScheduleCadence
from planner.sprints.logic import DateRange, current_sprint_id

_LOCAL_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_local_time(value: str) -> str:
    if _LOCAL_TIME.fullmatch(value) is None:
        raise PlannerError(
            ErrorCode.validation,
            "local_time must be an exact 24-hour HH:MM value",
            {"local_time": value},
        )
    return value


def current_local_time(now: datetime) -> str:
    return now.strftime("%H:%M")


def occurrence_key(now: datetime) -> str:
    """The local minute a schedule matched; repeated polls in it are one occurrence."""
    return now.isoformat(timespec="minutes")


def cadence_qualifies(
    cadence: ScheduleCadence,
    *,
    planning_day: str,
    sprint_ranges: tuple[DateRange, ...],
) -> bool:
    if cadence is ScheduleCadence.every_planning_day:
        return True
    sprint_id = current_sprint_id(planning_day, sprint_ranges)
    if sprint_id is None:
        return False
    sprint_range = next(item for item in sprint_ranges if item.id == sprint_id)
    if cadence is ScheduleCadence.current_sprint_day_four:
        sprint_day = (
            date.fromisoformat(planning_day)
            - date.fromisoformat(sprint_range.date_start)
        ).days + 1
        return sprint_day == 4
    return sprint_range.date_end == planning_day


def planning_day_for(now: datetime, boundary_hour: int) -> str:
    return planning_date(now, boundary_hour).isoformat()
