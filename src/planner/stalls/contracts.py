"""Shapes shared by everything that reports, checks or alarms on a stalled wait.

A tracked wait is anything Panels is waiting on that has an owner, a start time and a
limit. In-process waits register in the process-wide registry (stalls.registry.WAITS);
waits that live in database rows or in a service's memory come from stall sources.
The checker turns overdue waits into findings, and the watchdog alarms on findings.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class TrackedWaitKind(StrEnum):
    # The same (conversation_id, oldest_sequence) in consecutive continuous leases.
    mac_items_not_moving = "mac_items_not_moving"
    # A mailbox row still waiting to be fetched.
    mac_command_unfetched = "mac_command_unfetched"
    # A stop command without its proof (an accepted child_stopped, or a settled result).
    mac_stop_unproven = "mac_stop_unproven"
    # An open run of a replaced incarnation whose drain has not finished.
    mac_replacement_pending = "mac_replacement_pending"
    # A turn the lease lists as working, with no tool running and no activity.
    mac_turn_silent = "mac_turn_silent"
    # Recent contact from the Mac, but no accepted lease.
    mac_lease_failing = "mac_lease_failing"
    # A Mac item the VPS is still saving (the inbox's still-settling wait).
    mac_item_still_saving = "mac_item_still_saving"
    # A VPS-only conversation save that keeps retrying ("stuck at write N").
    vps_save_retrying = "vps_save_retrying"
    # Retiring a child after a failed cancel, in a task owned by the executor. Paused
    # while the Mac is away: retirement waits on the Mac's stop proof, which an asleep
    # Mac cannot send.
    mac_cancel_retirement = "mac_cancel_retirement"
    # After a replaced process's drain, the VPS ending its turn interrupted and sending
    # the recovery prompt. VPS work alone, so not paused.
    vps_replacement_retiring = "vps_replacement_retiring"


# These depend on the Mac. They count only the Mac's present time since they began
# (stalls.logic.findings), so they pause while it is away. The others run on VPS time
# alone (mac_lease_failing is not paused because sleep makes no contact).
PAUSED_WHILE_MAC_AWAY: frozenset[TrackedWaitKind] = frozenset(
    (
        TrackedWaitKind.mac_items_not_moving,
        TrackedWaitKind.mac_command_unfetched,
        TrackedWaitKind.mac_stop_unproven,
        TrackedWaitKind.mac_replacement_pending,
        TrackedWaitKind.mac_turn_silent,
        TrackedWaitKind.mac_cancel_retirement,
    )
)


@dataclass(frozen=True, slots=True)
class TrackedWait:
    """One wait, as a registry entry or a source reports it at one check.

    wait_key is unique within its kind and stable across checks, so the same wait
    seen again updates the same finding. started_at_ms is the VPS clock.
    """

    kind: TrackedWaitKind
    wait_key: str
    conversation_id: str | None
    detail: str
    started_at_ms: int

    @property
    def finding_key(self) -> str:
        return f"{self.kind.value}:{self.wait_key}"


@dataclass(frozen=True, slots=True)
class StallFinding:
    """One stall_findings row. stalled_since_ms is when the overdue wait began;
    cleared_at_ms is set once a check no longer reports it as overdue."""

    finding_key: str
    kind: TrackedWaitKind
    conversation_id: str | None
    ticket_id: str | None
    ticket_title: str | None
    detail: str
    stalled_since_ms: int
    last_seen_at_ms: int
    cleared_at_ms: int | None


class StallSource(Protocol):
    """Waits that live outside the registry: database rows, or a service's memory.

    The checker calls this on a worker thread, inside one read transaction on conn. A
    source that reads its own memory returns a snapshot taken under its own lock and
    may ignore conn.
    """

    def tracked_waits(self, conn: sqlite3.Connection, now_ms: int) -> tuple[TrackedWait, ...]: ...


class TrackedWaitHandle(Protocol):
    def set_detail(self, detail: str) -> None:
        """Replace the wait's detail, such as the write number a retrying save is at."""
        ...


class WaitRegistry(Protocol):
    """The process-wide register of in-process waits. Safe from any thread."""

    def track(
        self,
        kind: TrackedWaitKind,
        wait_key: str,
        *,
        detail: str,
        conversation_id: str | None = None,
    ) -> AbstractContextManager[TrackedWaitHandle]:
        """Register a wait for the duration of the with-block, started now."""
        ...

    def snapshot(self) -> tuple[TrackedWait, ...]:
        """One wait per finding_key: the earliest started, so a wait tracked twice at
        once (two writers of one conversation) reports from its first start."""
        ...


class StallChecker(Protocol):
    """The in-server loop. Every STALL_CHECK_INTERVAL_SECONDS it gathers the registry and
    the sources, writes stall_findings, and writes the heartbeat."""

    def start(self) -> None: ...

    async def close(self) -> None: ...


class StallCheckerFactory(Protocol):
    """How core.server builds the checker. clock returns the VPS clock in milliseconds."""

    def __call__(
        self,
        db_path: str,
        registry: WaitRegistry,
        sources: Callable[[], tuple[StallSource, ...]],
        clock: Callable[[], int],
    ) -> StallChecker: ...
