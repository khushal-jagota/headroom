"""Presence rules, judged by the VPS clock alone. The Mac's wall clock is display only."""

from __future__ import annotations

from planner.executor.configuration import LEASE_CONTINUITY_SECONDS, MAC_AWAY_AFTER_SECONDS
from planner.executor.contracts import PresenceJudgement, PresenceRecord


def judge_presence(record: PresenceRecord | None, now_ms: int) -> PresenceJudgement:
    """Classify the Mac from its presence row.

    A refusal stands until the next accepted lease clears it. A lease that says the Mac
    is going to sleep makes it asleep at once. Otherwise the Mac is present until
    MAC_AWAY_AFTER_SECONDS pass without an accepted lease.
    """
    if record is None:
        return PresenceJudgement("never", None)
    if record.refusal is not None:
        return PresenceJudgement("refused", record.refusal.refused_at_ms)
    if record.last_lease_at_ms is None:
        return PresenceJudgement("never", None)
    if record.going_to_sleep_at_ms is not None:
        return PresenceJudgement("asleep", record.going_to_sleep_at_ms)
    if now_ms - record.last_lease_at_ms < MAC_AWAY_AFTER_SECONDS * 1000:
        return PresenceJudgement(
            "present",
            record.last_lease_at_ms
            if record.continuous_since_ms is None
            else record.continuous_since_ms,
        )
    return PresenceJudgement("not_heard_from", record.last_lease_at_ms)


def next_presence_change_ms(record: PresenceRecord | None) -> int | None:
    """When the presence class changes by time alone, with no new lease or refusal: a
    present Mac becomes not_heard_from MAC_AWAY_AFTER_SECONDS after its last lease. None
    when no class changes by time (never, asleep, refused, or no lease yet). Lets the
    lease handler schedule that announcement on a timer."""
    if (
        record is None
        or record.refusal is not None
        or record.last_lease_at_ms is None
        or record.going_to_sleep_at_ms is not None
    ):
        return None
    return record.last_lease_at_ms + int(MAC_AWAY_AFTER_SECONDS * 1000)


def continuous_since_after_lease(
    previous: PresenceRecord | None, *, new_incarnation: bool, now_ms: int
) -> int:
    """The continuous_since_ms an accepted lease stores.

    A new incarnation, a first lease, or a lease more than LEASE_CONTINUITY_SECONDS after
    the previous accepted one starts a new continuous stretch now.
    """
    if (
        new_incarnation
        or previous is None
        or previous.last_lease_at_ms is None
        or previous.continuous_since_ms is None
        or now_ms - previous.last_lease_at_ms > LEASE_CONTINUITY_SECONDS * 1000
    ):
        return now_ms
    return previous.continuous_since_ms


def lease_changes_presence_signal(
    previous: PresenceRecord | None,
    current: PresenceRecord,
    *,
    new_incarnation: bool,
    now_ms: int,
) -> bool:
    """Whether an accepted lease's commit should announce a change.

    Only what a screen shows changes: the presence class, a new incarnation, a cleared
    refusal, or a flip of the going-to-sleep flag. A plain lease commits quietly.
    """
    return (
        new_incarnation
        or (previous is not None and previous.refusal is not None)
        or judge_presence(previous, now_ms).presence != judge_presence(current, now_ms).presence
        or (previous is None or previous.going_to_sleep_at_ms is None)
        != (current.going_to_sleep_at_ms is None)
    )
