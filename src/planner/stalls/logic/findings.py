"""Which tracked waits are overdue: the stall rule, judged by the VPS clock alone.

Two steps, both pure:

- presence_waits derives the two waits that only the presence row can show:
  mac_items_not_moving (the lease names the same oldest sequence for a conversation)
  and mac_lease_failing (the Mac keeps making contact, but no lease is accepted). The
  checker carries a PresenceObservation from one check to the next, because one lease
  alone cannot say how long an oldest sequence has stood still.
- overdue_waits applies the limits. Every Mac-dependent kind counts only continuous
  present time: its clock starts at max(its own start, the start of the Mac's current
  continuous presence), and while the Mac is not continuously present it reports
  nothing. So sleep, and a dark wake of a few seconds, raise nothing.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from planner.executor.contracts import PresenceRecord
from planner.executor.logic.presence import continuous_presence_start
from planner.stalls.configuration import (
    MAC_LEASE_FAILING_RECENT_CONTACT_SECONDS,
    stall_limit_seconds,
)
from planner.stalls.contracts import (
    PAUSED_WHILE_MAC_AWAY,
    StallFinding,
    TrackedWait,
    TrackedWaitKind,
)

# There is one Mac, so its lease has one failing wait.
MAC_LEASE_FAILING_WAIT_KEY = "mac"


@dataclass(frozen=True, slots=True)
class ItemProgress:
    """The oldest unreceipted sequence a conversation's lease entry named, and when a
    lease first named it (the VPS time that lease was accepted)."""

    oldest_sequence: int
    first_seen_at_ms: int


@dataclass(frozen=True, slots=True)
class PresenceObservation:
    """What one check passes to the next. items is by conversation_id. contact_since_ms
    is when the current stretch of recent contact began, None while there is none."""

    items: Mapping[str, ItemProgress] = field(default_factory=lambda: MappingProxyType({}))
    contact_since_ms: int | None = None


@dataclass(frozen=True, slots=True)
class OverdueWait:
    """A wait past its limit. stalled_since_ms is when its clock started: its own start,
    or for a Mac-dependent kind the later start of continuous presence."""

    wait: TrackedWait
    stalled_since_ms: int


def items_wait_key(conversation_id: str, oldest_sequence: int) -> str:
    return f"{conversation_id}:{oldest_sequence}"


def observation_from_open_findings(findings: Iterable[StallFinding]) -> PresenceObservation:
    """Rebuild what a previous checker process had seen, from its open findings.

    Only an overdue wait has a row, and its stalled_since_ms is no earlier than the
    start it counted from, so a restart never makes a stall look older than it is.
    """
    items: dict[str, ItemProgress] = {}
    contact_since_ms = None
    for finding in findings:
        if finding.cleared_at_ms is not None:
            continue
        if finding.kind is TrackedWaitKind.mac_items_not_moving and finding.conversation_id:
            sequence = finding.finding_key.rsplit(":", 1)[-1]
            if sequence.isdigit():
                items[finding.conversation_id] = ItemProgress(
                    int(sequence), finding.stalled_since_ms
                )
        elif finding.kind is TrackedWaitKind.mac_lease_failing:
            contact_since_ms = finding.stalled_since_ms
    return PresenceObservation(MappingProxyType(items), contact_since_ms)


def presence_waits(
    record: PresenceRecord | None, now_ms: int, previous: PresenceObservation
) -> tuple[tuple[TrackedWait, ...], PresenceObservation]:
    """The waits the presence row shows at now_ms, and the observation for next time."""
    items = _observe_items(record, now_ms, previous.items)
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
    contact_since_ms = _contact_since(record, now_ms, previous.contact_since_ms)
    if record is not None and contact_since_ms is not None:
        last_lease = record.last_lease_at_ms
        detail = "The Mac keeps making contact, but none of its leases is accepted"
        if record.refusal is not None:
            detail += f" ({record.refusal.reason})"
        waits.append(
            TrackedWait(
                TrackedWaitKind.mac_lease_failing,
                MAC_LEASE_FAILING_WAIT_KEY,
                None,
                detail,
                contact_since_ms if last_lease is None else max(last_lease, contact_since_ms),
            )
        )
    return tuple(waits), PresenceObservation(MappingProxyType(items), contact_since_ms)


def _observe_items(
    record: PresenceRecord | None, now_ms: int, previous: Mapping[str, ItemProgress]
) -> dict[str, ItemProgress]:
    # A lease is evidence only while the Mac is continuously present. While it is not,
    # keep what was seen: the item rule is paused anyway, and when the Mac returns its
    # clock restarts from the new stretch of presence.
    if record is None or record.lease is None or record.last_lease_at_ms is None:
        return dict(previous)
    if continuous_presence_start(record, now_ms) is None:
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
    record: PresenceRecord | None, now_ms: int, previous_since_ms: int | None
) -> int | None:
    if record is None or record.last_contact_at_ms is None:
        return None
    if now_ms - record.last_contact_at_ms > MAC_LEASE_FAILING_RECENT_CONTACT_SECONDS * 1000:
        return None
    # Counting from the start of this stretch of contact, not from the last lease, keeps
    # one refused lease right after a long sleep from looking like hours of failure.
    return record.last_contact_at_ms if previous_since_ms is None else previous_since_ms


def overdue_waits(
    waits: Iterable[TrackedWait], record: PresenceRecord | None, now_ms: int
) -> tuple[OverdueWait, ...]:
    """The waits past their limit at now_ms, one per finding_key (the earliest started)."""
    continuous_since_ms = continuous_presence_start(record, now_ms)
    earliest: dict[str, TrackedWait] = {}
    for wait in waits:
        kept = earliest.get(wait.finding_key)
        if kept is None or wait.started_at_ms < kept.started_at_ms:
            earliest[wait.finding_key] = wait
    tool_running = _conversations_with_tool_running(record)
    overdue: list[OverdueWait] = []
    for wait in earliest.values():
        started_at_ms = wait.started_at_ms
        if wait.kind in PAUSED_WHILE_MAC_AWAY:
            if continuous_since_ms is None:
                continue
            started_at_ms = max(started_at_ms, continuous_since_ms)
        if wait.kind is TrackedWaitKind.mac_turn_silent and wait.conversation_id in tool_running:
            continue
        if now_ms - started_at_ms >= stall_limit_seconds(wait.kind) * 1000:
            overdue.append(OverdueWait(wait, started_at_ms))
    return tuple(sorted(overdue, key=lambda item: item.wait.finding_key))


def _conversations_with_tool_running(record: PresenceRecord | None) -> frozenset[str]:
    """A running tool exempts its turn: a long build is not a silent agent."""
    if record is None or record.lease is None:
        return frozenset()
    return frozenset(
        turn.child.conversation_id for turn in record.lease.working_turns if turn.tool_running
    )
