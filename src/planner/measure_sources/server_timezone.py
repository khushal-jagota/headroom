"""Read server-local transition rules without changing the shared Clock contract."""

import os
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def server_timezone() -> ZoneInfo:
    name = os.environ.get("TZ", "").removeprefix(":")
    if name:
        try:
            return ZoneInfo(name)
        except (ValueError, ZoneInfoNotFoundError):
            if Path(name).is_absolute():
                with Path(name).open("rb") as stream:
                    return ZoneInfo.from_file(stream)
            raise ValueError("source schedules require a valid server timezone") from None
    with Path("/etc/localtime").open("rb") as stream:
        return ZoneInfo.from_file(stream)
