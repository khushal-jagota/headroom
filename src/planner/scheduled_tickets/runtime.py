"""The single-server polling loop for exact-time scheduled Ticket creation."""

from __future__ import annotations

import logging
import threading
from time import monotonic

from planner.core.clock import Clock
from planner.core.db import connect
from planner.scheduled_tickets.contracts import ScheduledTicketOccurrence
from planner.scheduled_tickets.participants import SlotParticipant, TicketScheduleParticipant

_LOG = logging.getLogger(__name__)


class ScheduledTicketLoop:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        *,
        busy_timeout_ms: int = 5000,
        participants: tuple[SlotParticipant, ...] | None = None,
    ) -> None:
        self._participants = (
            participants if participants is not None else (TicketScheduleParticipant(),)
        )
        self._db_path = db_path
        self._clock = clock
        self._busy_timeout_ms = busy_timeout_ms
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def poll_once(self) -> list[ScheduledTicketOccurrence]:
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            local_now = self._clock.now()
            results: list[ScheduledTicketOccurrence] = []
            for participant in self._participants:
                results.extend(
                    participant.poll(conn, local_now=local_now, now=int(local_now.timestamp()))
                )
            return results
        finally:
            conn.close()

    def start(self, interval: int) -> None:
        if self._thread is not None:
            raise RuntimeError("scheduled Ticket loop already started")
        for participant in self._participants:
            participant.start()
        self._thread = threading.Thread(
            target=self._run_loop,
            args=(interval,),
            name="scheduled-ticket-loop",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, deadline: float | None = None) -> None:
        self._stop.set()
        for participant in self._participants:
            participant.stop(deadline=deadline)
        thread = self._thread
        if thread is not None:
            # Occurrence work is synchronous and bounded by SQLite's busy timeout.
            timeout = 10.0 if deadline is None else max(0.0, deadline - monotonic())
            thread.join(timeout=timeout)
            if thread.is_alive():
                raise RuntimeError("schedule clock remains active; polling lock is retained")
            self._thread = None

    def _run_loop(self, interval: int) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                _LOG.exception("scheduled Ticket poll failed")
            self._stop.wait(interval)
