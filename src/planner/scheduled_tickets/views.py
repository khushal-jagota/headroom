"""JSON views for scheduled Ticket configuration and occurrence receipts."""

from __future__ import annotations

from planner.core.contracts import JsonDict
from planner.scheduled_tickets.contracts import (
    ScheduledTicketOccurrence,
    ScheduledTicketSchedule,
)


def occurrence_json(occurrence: ScheduledTicketOccurrence) -> JsonDict:
    return {
        "schedule_id": occurrence.schedule_id,
        "occurrence_key": occurrence.occurrence_key,
        "target_day_id": occurrence.target_day_id,
        "outcome": occurrence.outcome.value,
        "ticket_id": occurrence.ticket_id,
        "error": occurrence.error,
        "created_at": occurrence.created_at,
    }


def schedule_json(
    schedule: ScheduledTicketSchedule,
    occurrences: list[ScheduledTicketOccurrence] | None = None,
) -> JsonDict:
    result: JsonDict = {
        "id": schedule.id,
        "enabled": schedule.enabled,
        "cadence": schedule.cadence.value,
        "local_time": schedule.local_time,
        "title": schedule.template.title,
        "worker_type": schedule.template.worker_type,
        "kickoff_note": schedule.template.kickoff_note,
        "priority": schedule.template.priority.value,
        "deadline": schedule.template.deadline,
        "project_id": schedule.template.project_id,
        "sprint_id": schedule.template.sprint_id,
        "sprint_item_id": schedule.template.sprint_item_id,
        "employee_backend": schedule.template.employee_backend,
        "employee_launch_model": schedule.template.employee_launch_model,
        "blocked_by_ticket_ids": list(schedule.template.blocked_by_ticket_ids),
        "created_at": schedule.created_at,
        "updated_at": schedule.updated_at,
    }
    if occurrences is not None:
        result["occurrences"] = [occurrence_json(item) for item in occurrences]
    return result
