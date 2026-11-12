"""Exact local time validation for measure source schedules."""

from __future__ import annotations

import re

from planner.core.errors import ErrorCode, PlannerError

_LOCAL_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def validate_local_time(value: str) -> str:
    if _LOCAL_TIME.fullmatch(value) is None:
        raise PlannerError(
            ErrorCode.validation,
            "local_time must be an exact 24-hour HH:MM value",
            {"local_time": value},
        )
    return value
