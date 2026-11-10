"""Refresh persisted goal achievements on the existing background clock."""

import sqlite3
from datetime import datetime

from planner.goals.calendar import server_date
from planner.goals.outcomes import reconcile_completions
from planner.measures.data import transaction
from planner.resource_versions.data import source_revision


class GoalCompletionParticipant:
    def __init__(self) -> None:
        self._stopped = False
        self._previous: tuple[str, str, str] | None = None

    def start(self) -> None:
        self._stopped = False
        self._previous = None

    def poll(self, conn: sqlite3.Connection, *, local_now: datetime, now: int) -> None:
        if self._stopped:
            return
        today = server_date(now)

        def revision() -> tuple[str, str, str]:
            return (
                source_revision(conn, "goals", "all"),
                source_revision(conn, "automatic-measures", "all"),
                today,
            )

        if revision() != self._previous:
            # Capture the memo and the reconciled evidence in the same writer
            # transaction. A later measurement commit must remain observable.
            with transaction(conn):
                reconcile_completions(conn, now=now)
                accepted_revision = revision()
            self._previous = accepted_revision

    def stop(self, *, deadline: float | None = None) -> None:
        self._stopped = True
