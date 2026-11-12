"""Resolve goal dates in the server calendar without changing process timezone state."""

import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def server_calendar(now: datetime) -> datetime:
    zone = os.environ.get("TZ")
    if zone:
        return now.astimezone(ZoneInfo(zone.removeprefix(":")))
    with Path("/etc/localtime").open("rb") as stream:
        return now.astimezone(ZoneInfo.from_file(stream))


def server_date(now: int) -> str:
    return server_calendar(datetime.fromtimestamp(now, UTC)).date().isoformat()
