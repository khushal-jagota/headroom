"""Clock-sensitive collection health, independent of persistence and HTTP."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from planner.measure_sources.settings import STATUS_SLOT_GRACE_SECONDS
from planner.measures.contracts import SourceHealth


def expected_slots(
    *, now_local: datetime, local_time: str, created_at: int
) -> tuple[int | None, int]:
    """Return the last elapsed slot and next status boundary in the server timezone.

    Caller supplies local aware time. A round trip rejects the spring gap. The
    first repeated hour supplies the one local-date receipt on an autumn transition.
    """
    hour, minute = (int(part) for part in local_time.split(":"))
    elapsed: int | None = None
    future: int | None = None
    anchor = max(now_local.date(), datetime.fromtimestamp(created_at, now_local.tzinfo).date())
    for delta in range(-2, 3):
        day = anchor + timedelta(days=delta)
        candidate = datetime.combine(day, time(hour, minute), tzinfo=now_local.tzinfo)
        timestamp = int(candidate.timestamp())
        round_trip = datetime.fromtimestamp(timestamp, now_local.tzinfo)
        if (round_trip.date(), round_trip.hour, round_trip.minute) != (day, hour, minute):
            continue
        boundary = timestamp + STATUS_SLOT_GRACE_SECONDS
        if timestamp < created_at:
            continue
        if boundary <= int(now_local.timestamp()):
            elapsed = timestamp if elapsed is None else max(elapsed, timestamp)
        else:
            future = boundary if future is None else min(future, boundary)
    # The bounded scan includes tomorrow even at a skipped spring slot.
    assert future is not None
    return elapsed, future


def collection_health(
    *,
    enabled: bool,
    failed: bool,
    latest_success_frozen_at: int | None,
    expected_slot: int | None,
) -> SourceHealth:
    if not enabled:
        return SourceHealth.disabled
    if failed:
        return SourceHealth.failed
    if expected_slot is not None and (
        latest_success_frozen_at is None or latest_success_frozen_at < expected_slot
    ):
        return SourceHealth.stale
    if latest_success_frozen_at is None:
        return SourceHealth.never_collected
    return SourceHealth.healthy
