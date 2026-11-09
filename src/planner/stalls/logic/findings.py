"""Which tracked waits are overdue: the stall rule, judged by the VPS clock alone.

Two steps, both pure. The checker carries a PresenceObservation from one check to the
next, because one presence row alone cannot say how long anything has lasted.

- presence_waits derives the two waits that only the presence row can show:
  mac_items_not_moving (the lease names the same oldest sequence for a conversation)
  and mac_lease_failing (the Mac keeps making contact, but no lease is accepted). It
  also records the Mac's present time: stretches of accepted leases.
- overdue_waits applies the limits. A Mac-dependent kind counts only the Mac's present
  time since the wait began, summed over every stretch. So sleep counts nothing, while
  a lossy link or a Mac that is awake only now and then still adds up to the limit.
  A finding once overdue stays overdue while its wait stands (the same key with the same
  start): the Mac going away, or the server restarting, neither clears it nor makes it
  alarm a second time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from planner.executor.contracts import PresenceRecord
from planner.stalls import configuration
from planner.stalls.configuration import stall_limit_seconds
from planner.stalls.contracts import (
    PAUSED_WHILE_MAC_AWAY,
    StallFinding,
    TrackedWait,
    TrackedWaitKind,
)

# There is one Mac, so its lease has one failing wait.
MAC_LEASE_FAILING_WAIT_KEY = "mac"
_MAC_LEASE_FAILING_FINDING_KEY = f"{TrackedWaitKind.mac_lease_failing.value}:mac"


@dataclass(frozen=True, slots=True)
class ItemProgress:
    """The oldest unreceipted sequence a conversation's lease entry named, and when a
    lease first named it (the VPS time that lease was accepted)."""

    oldest_sequence: int
    first_seen_at_ms: int


@dataclass(frozen=True, slots=True)
class PresenceObservation:
    """What one check passes to the next.

    items is by conversation_id. contact_since_ms is when the current stretch of recent
    contact began, None while there is none. present_stretches is the Mac's present
    time, oldest first: each is the first and last of a run of accepted leases no more
    than MAC_PRESENT_GAP_SECONDS apart. open_findings maps the findings left open by
    the last check to their stalled_since_ms.
    """

    items: Mapping[str, ItemProgress] = field(default_factory=lambda: MappingProxyType({}))
    contact_since_ms: int | None = None
    present_stretches: tuple[tuple[int, int], ...] = ()
    open_findings: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class OverdueWait:
    """A wait past its limit. stalled_since_ms is when the wait began."""

    wait: TrackedWait
    stalled_since_ms: int


def items_wait_key(conversation_id: str, oldest_sequence: int) -> str:
    return f"{conversation_id}:{oldest_sequence}"


def observation_from_open_findings(findings: Iterable[StallFinding]) -> PresenceObservation:
    """Rebuild what a previous checker process had seen, from its open findings.

    An open finding keeps its start, so it stays open, and does not alarm again, while
    its wait stands. Present time is not stored: the first check after a restart knows
    only the presence row's current run of leases.
    """
    items: dict[str, ItemProgress] = {}
    contact_since_ms = None
    open_findings: dict[str, int] = {}
    for finding in findings:
        if finding.cleared_at_ms is not None:
            continue
        open_findings[finding.finding_key] = finding.stalled_since_ms
        if finding.kind is TrackedWaitKind.mac_items_not_moving and finding.conversation_id:
            sequence = finding.finding_key.rsplit(":", 1)[-1]
            if sequence.isdigit():
                items[finding.conversation_id] = ItemProgress(
                    int(sequence), finding.stalled_since_ms
                )
        elif finding.kind is TrackedWaitKind.mac_lease_failing:
            contact_since_ms = finding.stalled_since_ms
    return PresenceObservation(
        MappingProxyType(items), contact_since_ms, (), MappingProxyType(open_findings)
    )


def presence_waits(
    record: PresenceRecord | None, now_ms: int, previous: PresenceObservation
) -> tuple[tuple[TrackedWait, ...], PresenceObservation]:
    """The waits the presence row shows at now_ms, and the observation for next time
    (its open_findings are still the previous check's)."""
    items = _observe_items(record, previous.items)
    waits = [
        TrackedWait(
            TrackedWaitKind.mac_items_not_moving,
            items_wait_key(conversation_id, progress.oldest_sequence),
            conversation_id,
            f"The Mac has not delivered item {progress.oldest_sequence}",
            progress.first_seen_at_ms,
        )
        for conversation_id, progress in sorted(items.items())
    ]
    contact_since_ms = _contact_since(record, now_ms, previous)
    stretches = _observe_present_time(record, previous.present_stretches)
    if record is not None and contact_since_ms is not None:
        detail = "The Mac keeps making contact, but its leases are not accepted"
        if record.refusal is not None:
            detail += f" ({record.refusal.reason})"
        # Failing since the end of the Mac's last counted present time: a lone accepted
        # lease, minutes apart from the next, keeps the Mac no more present than none.
        present_until_ms = _counted_until_ms(stretches)
        waits.append(
            TrackedWait(
                TrackedWaitKind.mac_lease_failing,
                MAC_LEASE_FAILING_WAIT_KEY,
                None,
                detail,
                contact_since_ms
                if present_until_ms is None
                else max(present_until_ms, contact_since_ms),
            )
        )
    return tuple(waits), replace(
        previous,
        items=MappingProxyType(items),
        contact_since_ms=contact_since_ms,
        present_stretches=stretches,
    )


def _observe_items(
    record: PresenceRecord | None, previous: Mapping[str, ItemProgress]
) -> dict[str, ItemProgress]:
    # Only an accepted lease changes the row's lease, so reading the same one again, as
    # every check does while the Mac is away, changes nothing.
    if record is None or record.lease is None or record.last_lease_at_ms is None:
        return dict(previous)
    observed: dict[str, ItemProgress] = {}
    for waiting in record.lease.waiting_conversations:
        kept = previous.get(waiting.conversation_id)
        # Sequences only rise, so the same oldest sequence at two checks means it stood
        # in every lease between them. A new one is dated by the lease that named it.
        observed[waiting.conversation_id] = (
            kept
            if kept is not None and kept.oldest_sequence == waiting.oldest_sequence
            else ItemProgress(waiting.oldest_sequence, record.last_lease_at_ms)
        )
    return observed


def _contact_since(
    record: PresenceRecord | None, now_ms: int, previous: PresenceObservation
) -> int | None:
    if record is None or record.last_contact_at_ms is None:
        return None
    recent_ms = configuration.MAC_LEASE_FAILING_RECENT_CONTACT_SECONDS * 1000
    if now_ms - record.last_contact_at_ms <= recent_ms:
        # Counting from the start of this stretch of contact, not from the last lease,
        # keeps one refused lease right after a long sleep from looking like hours of
        # failure.
        return (
            record.last_contact_at_ms
            if previous.contact_since_ms is None
            else previous.contact_since_ms
        )
    # Contact stopped: the Mac sleeps. A failure already alarmed stands until a lease is
    # accepted (which moves the wait's start), so waking to it does not alarm again.
    if _MAC_LEASE_FAILING_FINDING_KEY in previous.open_findings:
        return previous.contact_since_ms
    return None


def _observe_present_time(
    record: PresenceRecord | None, previous: tuple[tuple[int, int], ...]
) -> tuple[tuple[int, int], ...]:
    """Extend the present stretches with the row's last accepted lease.

    The row's continuous_since_ms starts its own run of leases at a shorter gap, so a run
    that starts within MAC_PRESENT_GAP_SECONDS of the last lease seen continues the
    stretch: two lost leases are not a sleep.
    """
    if record is None or record.last_lease_at_ms is None:
        return previous
    lease = record.last_lease_at_ms
    run_start = lease if record.continuous_since_ms is None else record.continuous_since_ms
    if previous:
        first, last = previous[-1]
        if lease <= last:
            return previous
        if run_start - last <= configuration.MAC_PRESENT_GAP_SECONDS * 1000:
            return (*previous[:-1], (first, lease))
        if last - first < configuration.MAC_PRESENT_STRETCH_MIN_SECONDS * 1000:
            # It ended too short to count (a dark wake), so it never will.
            previous = previous[:-1]
    return (*previous, (min(run_start, lease), lease))


def present_ms(stretches: Iterable[tuple[int, int]], since_ms: int) -> int:
    """The Mac's present time after since_ms. A stretch counts once it has lasted
    MAC_PRESENT_STRETCH_MIN_SECONDS, so a dark wake adds nothing."""
    shortest_ms = configuration.MAC_PRESENT_STRETCH_MIN_SECONDS * 1000
    return sum(
        max(0, last - max(first, since_ms))
        for first, last in stretches
        if last - first >= shortest_ms
    )


def _counted_until_ms(stretches: tuple[tuple[int, int], ...]) -> int | None:
    """The end of the last stretch that counts as present time."""
    shortest_ms = configuration.MAC_PRESENT_STRETCH_MIN_SECONDS * 1000
    for first, last in reversed(stretches):
        if last - first >= shortest_ms:
            return last
    return None


def forget_present_time_before(
    observation: PresenceObservation, waits: Iterable[TrackedWait]
) -> PresenceObservation:
    """Drop the stretches that ended before every Mac-dependent wait began. The last
    stretch may still grow, and the one before it may be the last counted one (which
    dates mac_lease_failing), so both are kept. Every earlier stretch counts."""
    stretches = observation.present_stretches
    starts = [wait.started_at_ms for wait in waits if wait.kind in PAUSED_WHILE_MAC_AWAY]
    if len(stretches) <= 2:
        return observation
    earliest = min(starts, default=stretches[-1][1])
    kept = tuple(stretch for stretch in stretches[:-2] if stretch[1] > earliest)
    return replace(observation, present_stretches=(*kept, *stretches[-2:]))


def overdue_waits(
    waits: Iterable[TrackedWait],
    record: PresenceRecord | None,
    now_ms: int,
    observation: PresenceObservation,
) -> tuple[OverdueWait, ...]:
    """The waits past their limit at now_ms, one per finding_key (the earliest started).

    observation gives the present time (from presence_waits) and the open findings.
    """
    earliest: dict[str, TrackedWait] = {}
    for wait in waits:
        kept = earliest.get(wait.finding_key)
        if kept is None or wait.started_at_ms < kept.started_at_ms:
            earliest[wait.finding_key] = wait
    tool_running = _conversations_with_tool_running(record)
    # A replaced process's turn being retired on the VPS has finished its drain: its
    # still-open run is the VPS's retire step, tracked on VPS time.
    retiring = {
        wait.conversation_id
        for wait in earliest.values()
        if wait.kind is TrackedWaitKind.vps_replacement_retiring
    }
    overdue: list[OverdueWait] = []
    for wait in earliest.values():
        if wait.kind is TrackedWaitKind.mac_turn_silent and wait.conversation_id in tool_running:
            continue
        if (
            wait.kind is TrackedWaitKind.mac_replacement_pending
            and wait.conversation_id in retiring
        ):
            continue
        if observation.open_findings.get(wait.finding_key) == wait.started_at_ms:
            counted_ms = None
        elif wait.kind in PAUSED_WHILE_MAC_AWAY:
            counted_ms = present_ms(observation.present_stretches, wait.started_at_ms)
        else:
            counted_ms = now_ms - wait.started_at_ms
        if counted_ms is None or counted_ms >= stall_limit_seconds(wait.kind) * 1000:
            overdue.append(OverdueWait(wait, wait.started_at_ms))
    return tuple(sorted(overdue, key=lambda item: item.wait.finding_key))


def _conversations_with_tool_running(record: PresenceRecord | None) -> frozenset[str]:
    """A running tool exempts its turn: a long build is not a silent agent."""
    if record is None or record.lease is None:
        return frozenset()
    return frozenset(
        turn.child.conversation_id for turn in record.lease.working_turns if turn.tool_running
    )
