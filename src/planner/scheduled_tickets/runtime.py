"""The single-server polling loop for exact-time scheduled Ticket creation."""

from __future__ import annotations

import logging
import threading
from time import monotonic

from planner.core.clock import Clock
from planner.core.db import connect
from planner.scheduled_tickets.actions import run_current_slot
from planner.scheduled_tickets.contracts import ScheduledTicketOccurrence

_LOG = logging.getLogger(__name__)


class ScheduledTicketLoop:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        boundary_hour: int,
        busy_timeout_ms: int = 5000,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._boundary_hour = boundary_hour
        self._busy_timeout_ms = busy_timeout_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> list[ScheduledTicketOccurrence]:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            return run_current_slot(
                conn,
                planning_now=self._clock.now(),
                now=self._clock.now_unix(),
                boundary_hour=self._boundary_hour,
            )
        finally:
            conn.close()

    def start(self, interval: int) -> None:
        if self._thread is not None:
            raise RuntimeError("scheduled Ticket loop already started")
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="scheduled-ticket-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, deadline: float | None = None) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            # Occurrence work is synchronous and bounded by SQLite's busy timeout.
            timeout = 10.0 if deadline is None else max(0.0, deadline - monotonic())
            thread.join(timeout=timeout)
            self._thread = None

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("scheduled Ticket poll failed")
            self._stop.wait(interval)
