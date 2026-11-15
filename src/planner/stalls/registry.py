"""The process-wide register of in-process waits, read by the stall checker.

    with WAITS.track(TrackedWaitKind.vps_save_retrying, conversation_id,
                     conversation_id=conversation_id, detail="stuck at write 1") as wait:
        ...
        wait.set_detail("stuck at write 2")

Only waits that exist in this process belong here: the inbox's still-settling wait, a
VPS save that keeps retrying, and work in tasks the executor owns. Waits that live in
database rows come from stall sources, so they survive a restart.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from itertools import count

from planner.executor.clock import now_ms
from planner.stalls.contracts import TrackedWait, TrackedWaitKind


class _Handle:
    def __init__(self, registry: ProcessWaitRegistry, entry_id: int) -> None:
        self._registry = registry
        self._entry_id = entry_id

    def set_detail(self, detail: str) -> None:
        self._registry._set_detail(self._entry_id, detail)


class ProcessWaitRegistry:
    def __init__(self, clock: Callable[[], int] = now_ms) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[int, TrackedWait] = {}
        self._ids = count(1)

    @contextmanager
    def track(
        self,
        kind: TrackedWaitKind,
        wait_key: str,
        *,
        detail: str,
        conversation_id: str | None = None,
    ) -> Iterator[_Handle]:
        wait = TrackedWait(kind, wait_key, conversation_id, detail, self._clock())
        with self._lock:
            entry_id = next(self._ids)
            self._entries[entry_id] = wait
        try:
            yield _Handle(self, entry_id)
        finally:
            with self._lock:
                del self._entries[entry_id]

    def _set_detail(self, entry_id: int, detail: str) -> None:
        with self._lock:
            wait = self._entries.get(entry_id)
            if wait is not None:
                self._entries[entry_id] = replace(wait, detail=detail)

    def snapshot(self) -> tuple[TrackedWait, ...]:
        """One wait per finding_key, keeping the earliest started."""
        earliest: dict[str, TrackedWait] = {}
        with self._lock:
            entries = tuple(self._entries.values())
        for wait in entries:
            kept = earliest.get(wait.finding_key)
            if kept is None or wait.started_at_ms < kept.started_at_ms:
                earliest[wait.finding_key] = wait
        return tuple(earliest.values())


WAITS = ProcessWaitRegistry()
