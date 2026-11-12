"""Exact local time validation for measure source schedules."""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from planner.core.errors import ErrorCode, PlannerError
from planner.measure_sources.settings import SCHEDULE_CATCHUP_SECONDS

_LOCAL_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_local_time(value: str) -> str:
    if _LOCAL_TIME.fullmatch(value) is None:
        raise PlannerError(
            ErrorCode.validation,
            "local_time must be an exact 24-hour HH:MM value",
            {"local_time": value},
        )
    return value


def latest_due_slot(
    local_time: str, local_now: datetime, *, system_local: bool = False
) -> datetime | None:
    """Return only the latest real daily occurrence inside the catch-up window."""
    if system_local:
        local_now = local_now.astimezone()
    hour, minute = map(int, local_time.split(":"))
    for days_ago in (0, 1):
        wall_time = (local_now - timedelta(days=days_ago)).replace(
            hour=hour, minute=minute, second=0, microsecond=0, fold=0
        )
        # RealClock carries today's fixed offset, not historical local zone rules.
        # Resolve each local wall time through the OS before comparing elapsed age.
        candidate = wall_time.replace(tzinfo=None).astimezone() if system_local else wall_time
        round_trip = datetime.fromtimestamp(
            candidate.timestamp(), None if system_local else local_now.tzinfo
        )
        # A nonexistent spring-forward wall time is not a scheduled occurrence.
        if (round_trip.date(), round_trip.hour, round_trip.minute) != (
            wall_time.date(), wall_time.hour, wall_time.minute
        ):
            continue
        age = local_now.timestamp() - candidate.timestamp()
        if 0 <= age <= SCHEDULE_CATCHUP_SECONDS:
            return candidate
    return None
