"""Participants in the measure source clock."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import datetime
from typing import Protocol

from planner.conversation.workspace_admission import WorkspaceAdmission


class SlotParticipant(Protocol):
    def start(self) -> None: ...
    def poll(self, conn: sqlite3.Connection, *, local_now: datetime, now: int) -> None: ...
    def stop(self, *, deadline: float | None = None) -> None: ...


type SlotParticipantFactory = Callable[[WorkspaceAdmission], tuple[SlotParticipant, ...]]
