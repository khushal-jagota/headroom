"""The stall rule: which waits alarm, when, and which never do.

Risk: a stall that never alarms (a limit too long, a wait counted from the wrong start,
present time lost at every gap of a lossy or intermittent link), an alarm for sleep, a
night of dark wakes or a draining backlog, which teaches the owner to ignore alarms, or
one stall alarming again after every sleep. Layer: the pure rule in
stalls/logic/findings.py, with the production limits and presence rules, stepped
through simulated time at the checker's real interval exactly as the checker steps it.
The checker and watchdog tests reuse it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from types import MappingProxyType

import pytest

from planner.execution_identity import ChildReference, ExecutorIdentity, TurnToken
from planner.executor.configuration import (
    CONTACT_FLUSH_INTERVAL_SECONDS,
    LEASE_CONTINUITY_SECONDS,
    LEASE_INTERVAL_SECONDS,
)
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
    forget_present_time_before,
    overdue_waits,
    presence_waits,
)

S = 1000
CHECK_MS = configuration.STALL_CHECK_INTERVAL_SECONDS * S
LEASE_MS = int(LEASE_INTERVAL_SECONDS * S)
IDENTITY = ExecutorIdentity(22, "rev", 5, "incarnation-1")
# panels-stall-watchdog.timer: OnUnitActiveSec=30s, AccuracySec=5s.
WATCHDOG_TIMER_ACCURACY_SECONDS = 5


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


def _present_for(first_ms: int, last_ms: int) -> PresenceObservation:
    return PresenceObservation(present_stretches=((first_ms, last_ms),))


def _checks(until_ms: int) -> Iterator[int]:
    yield from range(0, until_ms + 1, CHECK_MS)


def test_production_limits_with_leases_checker_and_watchdog_stay_within_five_minutes() -> None:
    # The pass condition: any stall alarms within 300 s of starting, while the Mac is
    # present. Each kind's worst case, in seconds:
    # - first sighting: an item is named by a lease (up to one lease interval) that the
    #   checker samples (up to one check later); a turn is first listed by a lease;
    #   failing leases after a sleep start at a contact flushed up to 10 s late and
    #   sampled up to one check later;
    # - the limit, in present time for a Mac-dependent kind, which counts up to the last
    #   accepted lease, so up to one lease interval behind;
    # - then up to one check, then up to one watchdog run (its timer plus accuracy).
    lease = LEASE_INTERVAL_SECONDS
    check = configuration.STALL_CHECK_INTERVAL_SECONDS
    watchdog = configuration.WATCHDOG_INTERVAL_SECONDS + WATCHDOG_TIMER_ACCURACY_SECONDS
    first_sighting = {
        TrackedWaitKind.mac_items_not_moving: lease + check,
        TrackedWaitKind.mac_turn_silent: lease,
        TrackedWaitKind.mac_lease_failing: CONTACT_FLUSH_INTERVAL_SECONDS + check,
    }
    worst = {
        kind: first_sighting.get(kind, 0)
        + stall_limit_seconds(kind)
        + (lease if kind in PAUSED_WHILE_MAC_AWAY else 0)
        + check
        + watchdog
        for kind in TrackedWaitKind
    }
    assert max(worst.values()) == worst[TrackedWaitKind.mac_items_not_moving] == 265
    assert all(seconds <= 300 for seconds in worst.values()), worst
    assert configuration.CHECKER_HEARTBEAT_STALE_SECONDS + watchdog <= 300


@pytest.mark.parametrize("kind", list(TrackedWaitKind))
def test_each_kind_alarms_at_its_limit_and_not_before(kind: TrackedWaitKind) -> None:
    record = _present(since_ms=0, last_lease_ms=0)
    limit_ms = stall_limit_seconds(kind) * S
    wait = _wait(kind, started_at_ms=0)
    before = limit_ms - 1
    assert overdue_waits([wait], record, before, _present_for(0, before)) == ()
    assert overdue_waits([wait], record, limit_ms, _present_for(0, limit_ms)) == (
        OverdueWait(wait, 0),
    )


def test_mac_kinds_count_only_present_time_and_vps_kinds_do_not_pause() -> None:
    long_ago = -3600 * S
    waits = [_wait(kind, long_ago, conversation_id=kind.value) for kind in TrackedWaitKind]
    vps_only = set(TrackedWaitKind) - PAUSED_WHILE_MAC_AWAY
    asleep = replace(_present(since_ms=long_ago, last_lease_ms=0), going_to_sleep_at_ms=0)
    # Away an hour (no present time since the waits began): only VPS-side waits, which
    # include the VPS's own retire step after a drain.
    away = PresenceObservation()
    assert {item.wait.kind for item in overdue_waits(waits, asleep, 10 * S, away)} == vps_only
    assert TrackedWaitKind.vps_replacement_retiring in vps_only
    # Back for 60 s: a Mac wait that began an hour ago has 60 s of present time.
    returned = _present(since_ms=0, last_lease_ms=60 * S)
    assert {
        item.wait.kind for item in overdue_waits(waits, returned, 60 * S, _present_for(0, 60 * S))
    } == vps_only
    command = _wait(TrackedWaitKind.mac_command_unfetched, long_ago)
    at_limit = configuration.COMMAND_UNFETCHED_LIMIT_SECONDS * S
    # Dated from when it began, not from the Mac's return.
    assert overdue_waits([command], returned, at_limit, _present_for(0, at_limit)) == (
        OverdueWait(command, long_ago),
    )


def test_the_vps_retire_step_replaces_its_drained_runs_pending_wait() -> None:
    # Once the drain is reached, the run still open in the rows is the VPS's retire
    # step: one finding, on VPS time, even while the Mac is away.
    pending = TrackedWait(TrackedWaitKind.mac_replacement_pending, "c1", "c1", "rows", 0)
    retiring = TrackedWait(TrackedWaitKind.vps_replacement_retiring, "c1", "c1", "retire", 5 * S)
    now_ms = 5 * S + configuration.VPS_REPLACEMENT_RETIRING_LIMIT_SECONDS * S
    back = _present_for(0, now_ms)
    assert overdue_waits([pending, retiring], None, now_ms, back) == (OverdueWait(retiring, 5 * S),)
    assert overdue_waits([pending], None, now_ms, back) == (OverdueWait(pending, 0),)


Mac = Callable[[int], PresenceRecord | None]
Waits = Callable[[int], tuple[TrackedWait, ...]]


@dataclass
class Episode:
    """One stall finding from the check that opened it to the check that cleared it."""

    finding_key: str
    stalled_since_ms: int
    opened_at_ms: int
    cleared_at_ms: int | None = None


def _simulate(mac: Mac, until_ms: int, waits: Waits = lambda now_ms: ()) -> list[Episode]:
    """Step the rule at every check as StallChecker._check does. mac(now_ms) gives the
    presence row; waits(now_ms) the other sources' waits."""
    observation = PresenceObservation()
    episodes: dict[str, Episode] = {}
    every: list[Episode] = []
    for now_ms in _checks(until_ms):
        record = mac(now_ms)
        lease_waits, observation = presence_waits(record, now_ms, observation)
        all_waits = (*waits(now_ms), *lease_waits)
        overdue = overdue_waits(all_waits, record, now_ms, observation)
        for item in overdue:
            key = item.wait.finding_key
            episode = episodes.get(key)
            if episode is None:
                episodes[key] = Episode(key, item.stalled_since_ms, now_ms)
                every.append(episodes[key])
            else:
                assert episode.stalled_since_ms == item.stalled_since_ms
        for key in set(episodes) - {item.wait.finding_key for item in overdue}:
            episodes.pop(key).cleared_at_ms = now_ms
        observation = replace(
            forget_present_time_before(observation, all_waits),
            open_findings=MappingProxyType(
                {item.wait.finding_key: item.stalled_since_ms for item in overdue}
            ),
        )
    return every


def _leasing_mac(
    *, awake: Callable[[int], bool], oldest: Callable[[int], int], every_ms: int = LEASE_MS
) -> Mac:
    """A Mac whose leases are accepted every every_ms while awake(t), naming one waiting
    conversation whose oldest sequence is oldest(t). The row's continuous_since_ms
    follows the lease writer's own continuity rule."""

    def at(now_ms: int) -> PresenceRecord | None:
        leases = [t for t in range(0, now_ms + 1, every_ms) if awake(t)]
        if not leases:
            return None
        last = leases[-1]
        since = last
        for earlier in reversed(leases[:-1]):
            if since - earlier > LEASE_CONTINUITY_SECONDS * S:
                break
            since = earlier
        return _present(since, last, _lease((("c1", oldest(last)),)))

    return at


def _command_from(started_at_ms: int) -> Waits:
    command = TrackedWait(TrackedWaitKind.mac_command_unfetched, "cmd", "c1", "x", started_at_ms)
    return lambda now_ms: (command,) if now_ms >= started_at_ms else ()


ITEM = "mac_items_not_moving:c1:7"
COMMAND = "mac_command_unfetched:cmd"


def test_a_night_of_dark_wakes_raises_nothing() -> None:
    # Eight hours asleep with a backlog and a command waiting from the start. Every two
    # minutes the Mac wakes for 15 s, long enough for two accepted leases, and sends
    # nothing. Each dark wake is too short to count as present time.
    mac = _leasing_mac(awake=lambda t: t % (120 * S) < 15 * S, oldest=lambda t: 7)
    assert _simulate(mac, 8 * 3600 * S, _command_from(0)) == []


def test_a_draining_backlog_raises_nothing_and_a_stuck_one_alarms() -> None:
    # Waking to a long backlog: the oldest sequence moves at every lease.
    draining = _leasing_mac(awake=lambda t: True, oldest=lambda t: 1 + t // LEASE_MS)
    assert _simulate(draining, 30 * 60 * S) == []
    stuck = _leasing_mac(awake=lambda t: True, oldest=lambda t: 7)
    (episode,) = _simulate(stuck, 30 * 60 * S)
    assert (episode.finding_key, episode.cleared_at_ms) == (ITEM, None)
    limit_ms = configuration.MAC_ITEM_NO_PROGRESS_LIMIT_SECONDS * S
    assert limit_ms <= episode.opened_at_ms <= limit_ms + LEASE_MS + CHECK_MS


@pytest.mark.parametrize(
    "case,mac,alarm_by_s",
    [
        # Two leases of every three lost: accepted every 30 s.
        ("lossy link", _leasing_mac(awake=lambda t: True, oldest=lambda t: 7, every_ms=30 * S), 0),
        # Asleep 30 s of every 180 s, and 30 s of every 130 s.
        (
            "awake 150 s of 180 s",
            _leasing_mac(awake=lambda t: t % (180 * S) < 150 * S, oldest=lambda t: 7),
            0,
        ),
        (
            "awake 100 s of 130 s",
            _leasing_mac(awake=lambda t: t % (130 * S) < 100 * S, oldest=lambda t: 7),
            0,
        ),
        # Away longer than the gap: present time adds up across stretches. The first has
        # 90 s from its first lease to its last; the second counts once it has lasted
        # 60 s (the lease at 460 s, seen by the check at 465 s), bringing 150 s.
        (
            "awake 100 s of 400 s",
            _leasing_mac(awake=lambda t: t % (400 * S) < 100 * S, oldest=lambda t: 7),
            465,
        ),
    ],
)
def test_a_stuck_command_and_item_alarm_on_a_lossy_or_intermittent_link(
    case: str, mac: Mac, alarm_by_s: int
) -> None:
    simulated = _simulate(mac, 2 * 3600 * S, _command_from(0))
    episodes = {episode.finding_key: episode for episode in simulated}
    assert set(episodes) == {COMMAND, ITEM}, case
    # One episode each, open from the alarm to the end: a gap neither clears nor
    # re-dates it.
    assert all(episode.cleared_at_ms is None for episode in episodes.values()), case
    assert episodes[COMMAND].stalled_since_ms == 0
    if alarm_by_s == 0:
        # As good as always present: the limit plus the gap since the last accepted
        # lease (30 s at most here) plus one check.
        alarm_by_s = configuration.COMMAND_UNFETCHED_LIMIT_SECONDS + 30 + 15
    assert episodes[COMMAND].opened_at_ms <= alarm_by_s * S, case


def test_an_alarmed_stall_stays_one_finding_while_the_mac_sleeps_and_returns() -> None:
    # Stuck from the start; the Mac sleeps from 10 to 70 minutes, then returns with the
    # same backlog and the same command, and the item finally moves at 90 minutes.
    def awake(t: int) -> bool:
        return not 600 * S <= t < 4200 * S

    mac = _leasing_mac(awake=awake, oldest=lambda t: 7 if t < 5400 * S else t // LEASE_MS)
    command = _command_from(0)
    episodes = _simulate(mac, 100 * 60 * S, lambda now_ms: command(now_ms))
    assert [(e.finding_key, e.stalled_since_ms, e.cleared_at_ms) for e in episodes] == [
        (COMMAND, 0, None),
        (ITEM, 0, 5400 * S),
    ]


def _failing_mac(
    *,
    last_accepted_ms: int | None,
    contact_from_ms: int,
    every_ms: int = LEASE_MS,
    awake: Callable[[int], bool] = lambda t: True,
    refusal: str | None = "Mac needs update",
) -> Mac:
    """Lease attempts every every_ms from contact_from_ms while awake(t), none accepted.
    Each attempt is contact; the row's contact time lags by up to one flush."""
    flush_ms = int(CONTACT_FLUSH_INTERVAL_SECONDS * S)

    def at(now_ms: int) -> PresenceRecord | None:
        attempts = [t for t in range(contact_from_ms, now_ms - flush_ms + 1, every_ms) if awake(t)]
        contact = attempts[-1] if attempts else last_accepted_ms
        return PresenceRecord(
            executor_incarnation=IDENTITY.executor_incarnation,
            identity=IDENTITY,
            last_contact_at_ms=contact,
            last_lease_at_ms=last_accepted_ms,
            continuous_since_ms=last_accepted_ms,
            going_to_sleep_at_ms=None,
            lease=None,
            refusal=(
                None
                if refusal is None
                else ExecutorRefusal(refusal, IDENTITY, IDENTITY, contact_from_ms)
            ),
        )

    return at


@pytest.mark.parametrize("every_ms", [LEASE_MS, 30 * S])
def test_failing_leases_alarm_within_the_limit_even_on_a_lossy_link(every_ms: int) -> None:
    # Leased fine until 0, then every lease fails: refused, or failing with no refusal
    # (a malformed body, a database error) where only the attempt itself is contact.
    for refusal in ("Mac needs update", None):
        mac = _failing_mac(
            last_accepted_ms=0, contact_from_ms=10 * S, every_ms=every_ms, refusal=refusal
        )
        (episode,) = _simulate(mac, 10 * 60 * S)
        assert episode.finding_key == "mac_lease_failing:mac"
        limit_ms = configuration.MAC_LEASE_FAILING_LIMIT_SECONDS * S
        assert episode.opened_at_ms <= limit_ms + CHECK_MS


def test_leases_accepted_only_minutes_apart_alarm_as_failing() -> None:
    # Present until 600 s; then the Mac keeps trying every 10 s, but only one lease in
    # seven is accepted. Each lone lease is too short a stretch to count as present
    # time, so it must not restart the failing clock either.
    leasing = _leasing_mac(awake=lambda t: t < 600 * S or t % (70 * S) == 0, oldest=lambda t: 7)
    flush_ms = int(CONTACT_FLUSH_INTERVAL_SECONDS * S)

    def mac(now_ms: int) -> PresenceRecord | None:
        record = leasing(now_ms)
        assert record is not None
        attempt = (now_ms - flush_ms) // LEASE_MS * LEASE_MS
        return replace(record, last_contact_at_ms=max(attempt, record.last_lease_at_ms or 0))

    failing = [
        episode
        for episode in _simulate(mac, 30 * 60 * S)
        if episode.finding_key == "mac_lease_failing:mac"
    ]
    # Dated from the last lease within MAC_PRESENT_GAP_SECONDS of full presence.
    (episode,) = failing
    assert episode.stalled_since_ms <= 600 * S + configuration.MAC_PRESENT_GAP_SECONDS * S
    limit_ms = configuration.MAC_LEASE_FAILING_LIMIT_SECONDS * S
    assert episode.opened_at_ms <= episode.stalled_since_ms + limit_ms + CHECK_MS


def _napping_mac(*, cycle_ms: int, leases_per_wake: int) -> Mac:
    """Back-to-back naps: each wake gets leases_per_wake accepted leases 10 s apart, then
    a poll's contact 8 s after the last of them, then sleep until the next cycle."""

    def at(now_ms: int) -> PresenceRecord | None:
        leases = [
            start + i * LEASE_MS
            for start in range(0, now_ms + 1, cycle_ms)
            for i in range(leases_per_wake)
            if start + i * LEASE_MS <= now_ms
        ]
        last = leases[-1]
        since = last // cycle_ms * cycle_ms  # this wake's first lease
        contact = last + 8 * S if last + 8 * S <= now_ms else last
        record = _present(since, last, _lease((("c1", 7),)))
        return replace(record, last_contact_at_ms=contact)

    return at


@pytest.mark.parametrize("leases_per_wake", [1, 2])
def test_short_accepted_stretches_between_naps_raise_nothing(leases_per_wake: int) -> None:
    # Naps of about 55 s back to back, with a backlog and a command waiting. Contact
    # never stops for a minute, and no stretch is long enough to count, but every lease
    # is accepted: this is a sleeping Mac, not a failing lease.
    mac = _napping_mac(
        cycle_ms=(62 if leases_per_wake == 1 else 75) * S, leases_per_wake=leases_per_wake
    )
    assert _simulate(mac, 3600 * S, _command_from(0)) == []


def test_one_refused_lease_after_a_long_sleep_does_not_alarm_at_once() -> None:
    # Last accepted lease an hour before; contact starts again at wake. The failing
    # clock starts at the wake, not at the last lease.
    wake = 3600 * S
    mac = _failing_mac(last_accepted_ms=0, contact_from_ms=wake)
    (episode,) = _simulate(mac, wake + 10 * 60 * S)
    assert episode.opened_at_ms >= wake + configuration.MAC_LEASE_FAILING_LIMIT_SECONDS * S


def test_an_alarmed_lease_failure_stays_one_finding_across_a_sleep() -> None:
    # Refused from 10 s, asleep from 10 to 70 minutes, refused again after waking.
    def awake(t: int) -> bool:
        return not 600 * S <= t < 4200 * S

    mac = _failing_mac(last_accepted_ms=0, contact_from_ms=10 * S, awake=awake)
    (episode,) = _simulate(mac, 80 * 60 * S)
    assert (episode.finding_key, episode.cleared_at_ms) == ("mac_lease_failing:mac", None)


def test_a_silent_turn_alarms_unless_its_tool_is_running() -> None:
    turn = WorkingTurn(
        ChildReference("incarnation-1", "child", "c1"), TurnToken("c1", 3), tool_running=False
    )
    silent = _wait(TrackedWaitKind.mac_turn_silent, started_at_ms=0)
    now_ms = configuration.MAC_TURN_SILENT_LIMIT_SECONDS * S
    present = _present_for(0, now_ms)
    thinking = _present(0, now_ms, _lease(working=(turn,)))
    assert [item.wait for item in overdue_waits([silent], thinking, now_ms, present)] == [silent]
    building = _present(0, now_ms, _lease(working=(replace(turn, tool_running=True),)))
    assert overdue_waits([silent], building, now_ms, present) == ()
