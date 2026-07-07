"""Per-mind serialized run queue: at most ONE in-flight run per key; different
keys run concurrently; FIFO within a key. This is load-bearing correctness —
the gateway's 4009 busy-guard is per-process only and cannot protect two
child-per-run processes resuming the same stored session (notes.md:99-106).

The key must be a STABLE per-mind identifier. The queue guarantees at most one
in-flight run per key, FIFO within a key, and concurrency across keys; it is
generic over the item type ``T`` and prescribes nothing about item contents (the
run callable is injected). The mind's durable Hermes ``session_key``
(``stored_session_id``) is NOT a stable identifier — the gateway auto-compresses
and ROTATES it mid-run (spike 01) — so keying on the key value would split one
mind across two queue keys and let two children resume it concurrently, defeating
the very busy-guard this queue exists to enforce.

W3a's System B therefore keys on the STABLE ``ticket_id`` (one mind per ticket)
and resolves the mind's current stored ``session_key`` from the DB at EXECUTION
time inside the run (not at enqueue). This also subsumes kickoff: step-0 (no key
yet) and step-N of the same ticket share the ``ticket_id`` key, so the queue
serializes them — the run resolves ``None`` -> ``session.create`` and stores the
key, and the next run resolves it -> ``session.resume``. Two step-0 runs never
both create.

Module name note: ``planner.minds.queue`` cannot shadow the stdlib ``queue`` —
Python 3 imports are absolute, so ``import queue`` anywhere (including in this
package) resolves to the stdlib; this module is only reachable as
``planner.minds.queue``.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable

_log = logging.getLogger(__name__)


class MindQueue[T]:
    """FIFO-per-key serialized runner: one worker thread per active key.

    ``on_idle(key)`` (optional) fires once a key's queue has fully drained and the key
    has been removed from ``_active`` — the moment that mind is free again. It runs
    OUTSIDE the lock, so a callback that re-submits (or pokes a poller that will) cannot
    deadlock. This is the fast-path seam W3b's System A registers so a finished step
    immediately drives the next one instead of waiting a full tick."""

    def __init__(
        self, run: Callable[[str, T], None], *, on_idle: Callable[[str], None] | None = None
    ) -> None:
        self._run = run
        self._on_idle = on_idle
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._pending: dict[str, deque[T]] = {}
        self._active: set[str] = set()

    def submit(self, key: str, item: T) -> None:
        if not key:
            raise ValueError("queue key must be a non-empty string")
        with self._lock:
            self._pending.setdefault(key, deque()).append(item)
            if key in self._active:
                return
            self._active.add(key)
        threading.Thread(
            target=self._drain, args=(key,), name=f"mind-queue-{key}", daemon=True
        ).start()

    def is_active(self, key: str) -> bool:
        """Whether a run is enqueued or in-flight for this key. ``submit`` adds the key
        to ``_active`` synchronously before returning, so a caller that just submitted
        sees True — this closes the enqueue->start-write window a status-only readiness
        check would miss (W3b System A's has_inflight guard)."""
        with self._lock:
            return key in self._active

    def _drain(self, key: str) -> None:
        while True:
            item: T | None = None  # None is the idle sentinel; no caller ever enqueues None
            with self._lock:
                dq = self._pending.get(key)
                if not dq:
                    self._pending.pop(key, None)
                    self._active.discard(key)
                    self._idle.notify_all()
                else:
                    item = dq.popleft()
            if item is None:
                if self._on_idle is not None:
                    try:
                        self._on_idle(key)  # key already free; safe to re-submit from here
                    except Exception:
                        _log.exception("mind on_idle failed (key=%s)", key)
                return
            try:
                self._run(key, item)
            except Exception:
                _log.exception("mind run failed (key=%s)", key)

    def wait_idle(self, timeout: float | None = None) -> bool:
        with self._lock:
            return self._idle.wait_for(lambda: not self._active, timeout)
