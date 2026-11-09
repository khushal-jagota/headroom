"""The stall rule: which waits alarm, when, and which never do.

Risk: a stall that never alarms (a limit too long, a wait counted from the wrong start),
or an alarm for sleep, a dark wake or a draining backlog, which teaches the owner to
ignore alarms. Nothing covered these rules before this change. Layer: the pure rule in
stalls/logic/findings.py, with the production limits and presence rules, stepped through
simulated time at the checker's real interval. The checker and watchdog tests reuse it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import replace

import pytest

from planner.execution_identity import ChildReference, ExecutorIdentity, TurnToken
from planner.executor.configuration import LEASE_INTERVAL_SECONDS
from planner.executor.contracts import PresenceRecord
from planner.executor.protocol import (
    ExecutorRefusal,
    LeaseRequest,
    WaitingConversation,
    WorkingTurn,
)
from planner.stalls import configuration
from planner.stalls.configuration import stall_limit_seconds
from planner.stalls.contracts import PAUSED_WHILE_MAC_AWAY, TrackedWait, TrackedWaitKind
from planner.stalls.logic.findings import (
    OverdueWait,
    PresenceObservation,
    overdue_waits,
    presence_waits,
)

S = 1000
CHECK_MS = configuration.STALL_CHECK_INTERVAL_SECONDS * S
IDENTITY = ExecutorIdentity(22, "rev", 5, "incarnation-1")


def _lease(
    waiting: tuple[tuple[str, int], ...] = (), working: tuple[WorkingTurn, ...] = ()
) -> LeaseRequest:
    return LeaseRequest(
        identity=IDENTITY,
        process_id=1,
        mac_wall_clock_ms=0,
        queue_depth=len(waiting),
        oldest_item_age_ms=None,
        waiting_conversations=tuple(WaitingConversation(c, s) for c, s in waiting),
        waiting_conversations_truncated=False,
        working_turns=working,
        admission_stopped=False,
    )


def _present(
    since_ms: int, last_lease_ms: int, lease: LeaseRequest | None = None
) -> PresenceRecord:
    return PresenceRecord(
        executor_incarnation=IDENTITY.executor_incarnation,
        identity=IDENTITY,
        last_contact_at_ms=last_lease_ms,
        last_lease_at_ms=last_lease_ms,
        continuous_since_ms=since_ms,
        going_to_sleep_at_ms=None,
        lease=lease or _lease(),
        refusal=None,
    )


def _wait(kind: TrackedWaitKind, started_at_ms: int, conversation_id: str = "c1") -> TrackedWait:
    return TrackedWait(kind, f"{conversation_id}:w", conversation_id, "detail", started_at_ms)


def _checks(until_ms: int) -> Iterator[int]:
    yield from range(0, until_ms + 1, CHECK_MS)


def test_production_limits_with_checker_and_watchdog_stay_within_five_minutes() -> None:
    # The pass condition: any stall alarms within 300 s. A wait is overdue at its limit,
    # the checker sees it at most one interval later, the watchdog timer at most one
    # interval after that. An item stall is dated by the lease that first named it,
    # which the checker samples, so it can start up to one more check interval late.
    # A stopped checker alarms through its heartbeat.
    worst_limit = max(stall_limit_seconds(kind) for kind in TrackedWaitKind)
    sampling = configuration.STALL_CHECK_INTERVAL_SECONDS
    assert worst_limit == 180
    assert (
        worst_limit
        + sampling
        + configuration.STALL_CHECK_INTERVAL_SECONDS
        + configuration.WATCHDOG_INTERVAL_SECONDS
        <= 300
    )
    assert (
        configuration.CHECKER_HEARTBEAT_STALE_SECONDS + configuration.WATCHDOG_INTERVAL_SECONDS
        <= 300
    )


@pytest.mark.parametrize("kind", list(TrackedWaitKind))
def test_each_kind_alarms_at_its_limit_and_not_before(kind: TrackedWaitKind) -> None:
    record = _present(since_ms=0, last_lease_ms=0)
    limit_ms = stall_limit_seconds(kind) * S
    wait = _wait(kind, started_at_ms=0)
    assert overdue_waits([wait], replace(record, last_lease_at_ms=limit_ms - 1), limit_ms - 1) == ()
    assert overdue_waits([wait], replace(record, last_lease_at_ms=limit_ms), limit_ms) == (
        OverdueWait(wait, 0),
    )


def test_mac_kinds_pause_while_away_and_count_only_continuous_presence() -> None:
    long_ago = -3600 * S
    waits = [_wait(kind, long_ago, conversation_id=kind.value) for kind in TrackedWaitKind]
    asleep = replace(_present(since_ms=long_ago, last_lease_ms=0), going_to_sleep_at_ms=0)
    # Asleep (and so too for a Mac quiet past the continuity gap): only VPS-side waits.
    for record, now_ms in ((asleep, 10 * S), (_present(long_ago, 0), 30 * S)):
        assert {item.wait.kind for item in overdue_waits(waits, record, now_ms)} == {
            TrackedWaitKind.mac_lease_failing,
            TrackedWaitKind.mac_item_still_saving,
            TrackedWaitKind.vps_save_retrying,
        }
    # Back for 60 s: a Mac wait that began an hour ago counts from the return.
    returned = _present(since_ms=0, last_lease_ms=60 * S)
    assert {item.wait.kind for item in overdue_waits(waits, returned, 60 * S)} == (
        set(TrackedWaitKind) - PAUSED_WHILE_MAC_AWAY
    )
    command = _wait(TrackedWaitKind.mac_command_unfetched, long_ago)
    at_limit = configuration.COMMAND_UNFETCHED_LIMIT_SECONDS * S
    assert overdue_waits([command], replace(returned, last_lease_at_ms=at_limit), at_limit) == (
        OverdueWait(command, 0),
    )


Mac = Callable[[int], PresenceRecord | None]


def _simulate(mac: Mac, until_ms: int) -> list[tuple[int, tuple[OverdueWait, ...]]]:
    """Step the item and lease rules at every check. mac(now_ms) gives the presence row."""
    observation = PresenceObservation()
    raised = []
    for now_ms in _checks(until_ms):
        record = mac(now_ms)
        waits, observation = presence_waits(record, now_ms, observation)
        overdue = overdue_waits(waits, record, now_ms)
        if overdue:
            raised.append((now_ms, overdue))
    return raised


def _leasing_mac(*, awake: Callable[[int], bool], oldest: Callable[[int], int]) -> Mac:
    """A Mac that leases every LEASE_INTERVAL_SECONDS while awake(t), naming one waiting
    conversation whose oldest sequence is oldest(t)."""
    lease_ms = int(LEASE_INTERVAL_SECONDS * S)

    def at(now_ms: int) -> PresenceRecord | None:
        leases = [t for t in range(0, now_ms + 1, lease_ms) if awake(t)]
        if not leases:
            return None
        last = leases[-1]
        since = last
        for earlier in reversed(leases[:-1]):
            if since - earlier > 25 * S:
                break
            since = earlier
        return _present(since, last, _lease((("c1", oldest(last)),)))

    return at


def test_a_backlog_with_a_short_dark_wake_every_two_minutes_raises_nothing() -> None:
    # 30 simulated minutes: asleep, then awake for 5 s every 2 minutes, the backlog's
    # oldest item unchanged throughout (a dark wake sends a lease, nothing drains).
    mac = _leasing_mac(awake=lambda t: t % (120 * S) < 5 * S, oldest=lambda t: 7)
    assert _simulate(mac, 30 * 60 * S) == []


def test_a_draining_backlog_raises_nothing_and_a_stuck_one_alarms() -> None:
    # Waking to a long backlog: the oldest sequence moves at every lease.
    draining = _leasing_mac(
        awake=lambda t: True, oldest=lambda t: 1 + t // int(LEASE_INTERVAL_SECONDS * S)
    )
    assert _simulate(draining, 30 * 60 * S) == []
    stuck = _leasing_mac(awake=lambda t: True, oldest=lambda t: 7)
    first_now, first = _simulate(stuck, 30 * 60 * S)[0]
    assert [item.wait.finding_key for item in first] == ["mac_items_not_moving:c1:7"]
    assert first[0].wait.conversation_id == "c1"
    assert configuration.MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS * S <= first_now
    assert first_now <= (configuration.MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS * S + 2 * CHECK_MS)


def test_a_silent_turn_alarms_unless_its_tool_is_running() -> None:
    turn = WorkingTurn(
        ChildReference("incarnation-1", "child", "c1"), TurnToken("c1", 3), tool_running=False
    )
    silent = _wait(TrackedWaitKind.mac_turn_silent, started_at_ms=0)
    now_ms = configuration.MAC_TURN_SILENT_LIMIT_SECONDS * S
    thinking = _present(0, now_ms, _lease(working=(turn,)))
    assert [item.wait for item in overdue_waits([silent], thinking, now_ms)] == [silent]
    building = _present(0, now_ms, _lease(working=(replace(turn, tool_running=True),)))
    assert overdue_waits([silent], building, now_ms) == ()


def _refusing_mac(*, last_accepted_ms: int | None, contact_from_ms: int) -> Mac:
    """Refused leases every 10 s from contact_from_ms. Contact is flushed to the row at
    most every 10 s, so the row's contact time lags by up to that."""

    def at(now_ms: int) -> PresenceRecord | None:
        if now_ms < contact_from_ms:
            contact = last_accepted_ms
        else:
            contact = now_ms - (now_ms - contact_from_ms) % (10 * S)
        refusal = ExecutorRefusal("Mac needs update", IDENTITY, IDENTITY, contact_from_ms)
        return PresenceRecord(
            executor_incarnation=IDENTITY.executor_incarnation,
            identity=IDENTITY,
            last_contact_at_ms=contact,
            last_lease_at_ms=last_accepted_ms,
            continuous_since_ms=last_accepted_ms,
            going_to_sleep_at_ms=None,
            lease=None,
            refusal=refusal,
        )

    return at


def test_refused_leases_every_ten_seconds_alarm_within_225_seconds() -> None:
    # A Mac that leased fine until 0, then is refused at every lease: never paused.
    mac = _refusing_mac(last_accepted_ms=0, contact_from_ms=10 * S)
    first_now, first = _simulate(mac, 10 * 60 * S)[0]
    assert [item.wait.kind for item in first] == [TrackedWaitKind.mac_lease_failing]
    assert "Mac needs update" in first[0].wait.detail
    assert first_now + configuration.WATCHDOG_INTERVAL_SECONDS * S <= 225 * S


def test_one_refused_lease_after_a_long_sleep_does_not_alarm_at_once() -> None:
    # Last accepted lease an hour before; contact starts again at wake. The failing
    # clock starts at the wake, not at the last lease.
    wake = 3600 * S
    mac = _refusing_mac(last_accepted_ms=0, contact_from_ms=wake)
    raised = _simulate(mac, wake + 10 * 60 * S)
    assert raised[0][0] >= wake + configuration.MAC_LEASE_FAILING_LIMIT_SECONDS * S
