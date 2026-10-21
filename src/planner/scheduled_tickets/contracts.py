"""Stored scheduled-Ticket configuration and occurrence outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from planner.tickets.contracts import TicketLocale


class ScheduleCadence(StrEnum):
    daily = "daily"
    every_planning_day = "every_planning_day"
    current_sprint_day_four = "current_sprint_day_four"
    current_sprint_final_day = "current_sprint_final_day"


class OccurrenceOutcome(StrEnum):
    created = "created"
    suppressed = "suppressed"
    failed = "failed"


@dataclass(frozen=True)
class ScheduledTicketTemplate:
    title: str
    worker_type: str
    kickoff_note: str
    deadline: str | None
    project_id: str | None
    sprint_item_id: str | None
    employee_backend: str | None
    employee_launch_model: str | None
    blocked_by_ticket_ids: tuple[str, ...]
    orchestrator_kind: str = field(default="chief", kw_only=True)
    locale: TicketLocale | None = field(default=None, kw_only=True)


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
    target_date: str
    outcome: OccurrenceOutcome
    ticket_id: str | None
    error: str | None
    created_at: int
