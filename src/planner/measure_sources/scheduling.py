"""One source participant in the existing daily clock and machine-lock lifespan."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.errors import PlannerError
from planner.measure_sources.contracts import SourceRuntime
from planner.measure_sources.dispatcher import SourceDispatcher
from planner.measure_sources.run_actions import admit_scheduled_run
from planner.scheduled_tickets.contracts import ScheduledTicketOccurrence

_LOG = logging.getLogger(__name__)


class SourceScheduleParticipant:
    def __init__(self, config: Config, clock: Clock, runtime: SourceRuntime) -> None:
        self._stopped = False
        self._dispatcher = SourceDispatcher(
            config.db_path,
            clock,
            runtime,
            key_path=Path(config.credential_key_path),
            busy_timeout_ms=config.db_busy_timeout_ms,
        )

    def start(self) -> None:
        self._dispatcher.start()

    def poll(
        self, conn: sqlite3.Connection, *, local_now: datetime, now: int
    ) -> list[ScheduledTicketOccurrence]:
        if self._stopped:
            return []
        rows = conn.execute(
            "SELECT id FROM measure_sources WHERE enabled=1 AND local_time=? ORDER BY id",
            (local_now.strftime("%H:%M"),),
        ).fetchall()
        for row in rows:
            if self._stopped:
                break
            try:
                admit_scheduled_run(conn, str(row[0]), local_now=local_now, now=now)
            except PlannerError:
                _LOG.error("source schedule admission refused", extra={"source_id": str(row[0])})
        return []

    def stop(self, *, deadline: float | None = None) -> None:
        self._stopped = True
        self._dispatcher.stop(deadline=deadline)
