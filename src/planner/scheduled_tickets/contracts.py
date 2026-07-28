"""Stored scheduled-Ticket configuration and occurrence outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from planner.core.contracts import Priority


class ScheduleCadence(StrEnum):
    every_planning_day = "every_planning_day"
    current_sprint_final_day = "current_sprint_final_day"


class OccurrenceOutcome(StrEnum):
    created = "created"
    suppressed = "suppressed"
    failed = "failed"


class ScheduledTicketPlacementMode(StrEnum):
    current_sprint = "current_sprint"
    backlog = "backlog"
    sprint_item = "sprint_item"


@dataclass(frozen=True)
class ScheduledTicketTemplate:
    title: str
    worker_type: str
    kickoff_note: str
    priority: Priority
    deadline: str | None
    project_id: str | None
    placement_mode: ScheduledTicketPlacementMode
    sprint_item_id: str | None
    employee_backend: str | None
    employee_launch_model: str | None
    blocked_by_ticket_ids: tuple[str, ...]


@dataclass(frozen=True)
class ScheduledTicketSchedule:
    id: str
    enabled: bool
    cadence: ScheduleCadence
    local_time: str
    template: ScheduledTicketTemplate
    created_at: int
    updated_at: int


@dataclass(frozen=True)
class ScheduledTicketOccurrence:
    schedule_id: str
    occurrence_key: str
    target_day_id: str
    outcome: OccurrenceOutcome
    ticket_id: str | None
    error: str | None
    created_at: int
