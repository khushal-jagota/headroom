"""Daily slot participants; the return value preserves the existing Ticket poll API."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.scheduled_tickets.actions import run_current_slot
from planner.scheduled_tickets.contracts import ScheduledTicketOccurrence


class SlotParticipant(Protocol):
    def start(self) -> None: ...
    def poll(
        self, conn: sqlite3.Connection, *, local_now: datetime, now: int
    ) -> list[ScheduledTicketOccurrence]: ...
    def stop(self, *, deadline: float | None = None) -> None: ...


type SlotParticipantFactory = Callable[[WorkspaceAdmission], tuple[SlotParticipant, ...]]

class TicketScheduleParticipant:
    def start(self) -> None:
        pass

    def poll(
        self, conn: sqlite3.Connection, *, local_now: datetime, now: int
    ) -> list[ScheduledTicketOccurrence]:
        return run_current_slot(conn, local_now=local_now, now=now)

    def stop(self, *, deadline: float | None = None) -> None:
        pass
