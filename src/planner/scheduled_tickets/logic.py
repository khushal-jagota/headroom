"""Pure clock-slot and cadence rules for scheduled Ticket creation."""

from __future__ import annotations

import re
from datetime import datetime

from planner.core.errors import ErrorCode, PlannerError
from planner.scheduled_tickets.contracts import ScheduleCadence

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


def cadence_qualifies(cadence: ScheduleCadence) -> bool:
    """Historical calendar cadences remain inert."""
    return cadence is ScheduleCadence.daily
