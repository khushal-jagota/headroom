"""One source participant in the existing daily clock and machine-lock lifespan."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core.clock import Clock
from planner.core.config import DB_BUSY_TIMEOUT_MS, Config
from planner.core.errors import PlannerError
from planner.measure_sources.contracts import RuntimeLimits, SourceRuntime
from planner.measure_sources.dispatcher import SourceDispatcher
from planner.measure_sources.local_time import latest_due_slot
from planner.measure_sources.run_actions import admit_scheduled_run

_LOG = logging.getLogger(__name__)


class SourceScheduleParticipant:
    def __init__(
        self,
        config: Config,
        clock: Clock,
        runtime: SourceRuntime,
        *,
        workspace_admission: WorkspaceAdmission | None = None,
    ) -> None:
        self._stopped = False
        self._dispatcher = SourceDispatcher(
            config.db_path,
            clock,
            runtime,
            key_path=Path(config.credential_key_path),
            busy_timeout_ms=DB_BUSY_TIMEOUT_MS,
            runtime_limits=RuntimeLimits(config.measure_source_timeout_seconds),
            workspace_admission=workspace_admission,
        )

    def start(self) -> None:
        self._dispatcher.start()

    def poll(self, conn: sqlite3.Connection, *, local_now: datetime, now: int) -> None:
        if self._stopped:
            return
        rows = conn.execute(
            "SELECT id,local_time,created_at FROM measure_sources WHERE enabled=1 ORDER BY id",
        ).fetchall()
        for row in rows:
            if self._stopped:
                break
            slot = latest_due_slot(
                str(row["local_time"]), local_now, system_local=True
            )
            if slot is None or slot.timestamp() < int(row["created_at"]) // 60 * 60:
                continue
            try:
                admit_scheduled_run(conn, str(row[0]), local_now=slot, now=now)
            except PlannerError:
                _LOG.error("source schedule admission refused", extra={"source_id": str(row[0])})
        return

    def stop(self, *, deadline: float | None = None) -> None:
        self._stopped = True
        self._dispatcher.stop(deadline=deadline)
