"""Application actions for schedule management and exact-slot execution."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import cast

from planner.core.contracts import OWNER_PRINCIPAL, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.scheduled_tickets import data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketOccurrence,
    ScheduledTicketPlacementMode,
    ScheduledTicketSchedule,
    ScheduledTicketTemplate,
)
from planner.scheduled_tickets.logic import (
    cadence_qualifies,
    current_local_time,
    occurrence_key,
    planning_day_for,
    validate_local_time,
)
from planner.sprints.logic import DateRange
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS

_LOG = logging.getLogger(__name__)


def _validate_template(conn: sqlite3.Connection, template: ScheduledTicketTemplate) -> None:
    if (
        template.placement_mode is ScheduledTicketPlacementMode.backlog
        and template.sprint_id is not None
    ):
        raise PlannerError(
            ErrorCode.validation,
            "backlog scheduled Ticket cannot name a Sprint",
            {"sprint_id": template.sprint_id},
        )
    tickets_data.validate_ticket_creation_context(
        conn,
        title=template.title,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type=template.worker_type,
        employee_backend=template.employee_backend,
        employee_launch_model=template.employee_launch_model,
        project_id=template.project_id,
        sprint_id=template.sprint_id,
        deadline=template.deadline,
        sprint_item_id=template.sprint_item_id,
        blocked_by_ticket_ids=list(template.blocked_by_ticket_ids),
    )


def create_schedule(
    conn: sqlite3.Connection,
    *,
    enabled: bool,
    cadence: ScheduleCadence,
    local_time: str,
    template: ScheduledTicketTemplate,
    now: int,
) -> ScheduledTicketSchedule:
    with data.transaction(conn):
        if template.sprint_item_id is not None and template.project_id is None:
            item = conn.execute(
                "SELECT project_id FROM sprint_items WHERE id = ?",
                (template.sprint_item_id,),
            ).fetchone()
            if item is not None:
                template = replace(
                    template,
                    project_id=str(item["project_id"]),
                )
        _validate_template(conn, template)
        return data.create_schedule(
            conn,
            enabled=enabled,
            cadence=cadence,
            local_time=validate_local_time(local_time),
            template=template,
            now=now,
        )


def update_schedule(
    conn: sqlite3.Connection,
    schedule_id: str,
    changes: Mapping[str, object],
    *,
    now: int,
) -> ScheduledTicketSchedule:
    with data.transaction(conn):
        return _update_schedule_locked(conn, schedule_id, changes, now=now)


def _update_schedule_locked(
    conn: sqlite3.Connection,
    schedule_id: str,
    changes: Mapping[str, object],
    *,
    now: int,
) -> ScheduledTicketSchedule:
    current = data.read_schedule(conn, schedule_id)
    normalized_changes = dict(changes)
    raw_mode = normalized_changes.get("placement_mode", current.template.placement_mode)
    placement_mode = (
        raw_mode
        if isinstance(raw_mode, ScheduledTicketPlacementMode)
        else ScheduledTicketPlacementMode(str(raw_mode))
    )
    if placement_mode is ScheduledTicketPlacementMode.backlog:
        normalized_changes["sprint_id"] = None

    item_id = normalized_changes.get("sprint_item_id", current.template.sprint_item_id)
    if item_id is not None:
        item = conn.execute("SELECT project_id FROM sprint_items WHERE id=?", (item_id,)).fetchone()
        if item is not None:
            if (
                "project_id" in normalized_changes
                and normalized_changes["project_id"] != item["project_id"]
            ):
                raise PlannerError(ErrorCode.validation, "schedule Project does not match Outcome")
            normalized_changes["project_id"] = str(item["project_id"])

    template_values: dict[str, object] = {
        "title": current.template.title,
        "worker_type": current.template.worker_type,
        "kickoff_note": current.template.kickoff_note,
        "priority": current.template.priority,
        "deadline": current.template.deadline,
        "project_id": current.template.project_id,
        "sprint_id": current.template.sprint_id,
        "placement_mode": current.template.placement_mode,
        "sprint_item_id": current.template.sprint_item_id,
        "employee_backend": current.template.employee_backend,
        "employee_launch_model": current.template.employee_launch_model,
        "blocked_by_ticket_ids": current.template.blocked_by_ticket_ids,
    }
    for key in tuple(template_values):
        if key in normalized_changes:
            template_values[key] = normalized_changes[key]
    template = ScheduledTicketTemplate(
        title=str(template_values["title"]),
        worker_type=str(template_values["worker_type"]),
        kickoff_note=str(template_values["kickoff_note"]),
        priority=(
            template_values["priority"]
            if isinstance(template_values["priority"], Priority)
            else Priority(str(template_values["priority"]))
        ),
        deadline=(
            None if template_values["deadline"] is None else str(template_values["deadline"])
        ),
        project_id=(
            None if template_values["project_id"] is None else str(template_values["project_id"])
        ),
        sprint_id=(
            None if template_values["sprint_id"] is None else str(template_values["sprint_id"])
        ),
        placement_mode=(
            template_values["placement_mode"]
            if isinstance(template_values["placement_mode"], ScheduledTicketPlacementMode)
            else ScheduledTicketPlacementMode(str(template_values["placement_mode"]))
        ),
        sprint_item_id=(
            None
            if template_values["sprint_item_id"] is None
            else str(template_values["sprint_item_id"])
        ),
        employee_backend=(
            None
            if template_values["employee_backend"] is None
            else str(template_values["employee_backend"])
        ),
        employee_launch_model=(
            None
            if template_values["employee_launch_model"] is None
            else str(template_values["employee_launch_model"])
        ),
        blocked_by_ticket_ids=tuple(
            cast(tuple[str, ...], template_values["blocked_by_ticket_ids"])
        ),
    )
    _validate_template(conn, template)
    encoded = normalized_changes
    if "local_time" in encoded:
        encoded["local_time"] = validate_local_time(str(encoded["local_time"]))
    return data.update_schedule(conn, schedule_id, encoded, now=now)


def _sprint_ranges(conn: sqlite3.Connection) -> tuple[DateRange, ...]:
    return tuple(
        DateRange(
            id=str(row["id"]),
            date_start=str(row["date_start"]),
            date_end=str(row["date_end"]),
        )
        for row in conn.execute(
            "SELECT id, date_start, date_end FROM sprints ORDER BY date_start, id"
        ).fetchall()
    )


def _existing_ticket_for_schedule_on_day(
    conn: sqlite3.Connection,
    *,
    target_day_id: str,
    worker_type: str,
    title: str,
) -> str | None:
    title_clause = " AND tickets.title = ?" if worker_type == "personal" else ""
    parameters = (
        (target_day_id, worker_type, title)
        if worker_type == "personal"
        else (target_day_id, worker_type)
    )
    row = conn.execute(
        "SELECT tickets.id FROM tickets "
        "JOIN day_tickets ON day_tickets.ticket_id = tickets.id "
        "WHERE day_tickets.day_id = ? AND tickets.worker_type = ? "
        f"{title_clause} "
        "ORDER BY day_tickets.position, tickets.id LIMIT 1",
        parameters,
    ).fetchone()
    return None if row is None else str(row["id"])


def _settle_occurrence(
    conn: sqlite3.Connection,
    schedule: ScheduledTicketSchedule,
    *,
    planning_now: datetime,
    now: int,
    boundary_hour: int,
) -> ScheduledTicketOccurrence:
    key = occurrence_key(planning_now)
    # The planning date—not the calendar date—is the target. This remains explicit here
    # because a pre-5am exact time belongs to the previous planning day.
    target_day = f"day_{planning_day_for(planning_now, boundary_hour)}"
    try:
        with data.transaction(conn):
            existing_occurrence = data.read_occurrence(conn, schedule.id, key)
            if existing_occurrence is not None:
                return existing_occurrence
            existing_ticket_id = _existing_ticket_for_schedule_on_day(
                conn,
                target_day_id=target_day,
                worker_type=schedule.template.worker_type,
                title=schedule.template.title,
            )
            if existing_ticket_id is not None:
                return data.insert_occurrence(
                    conn,
                    schedule_id=schedule.id,
                    occurrence_key=key,
                    target_day_id=target_day,
                    outcome=OccurrenceOutcome.suppressed,
                    ticket_id=existing_ticket_id,
                    error=None,
                    now=now,
                )
            sprint_item_id = schedule.template.sprint_item_id
            project_id = schedule.template.project_id
            sprint_id = schedule.template.sprint_id
            sprint_id_explicit = (
                schedule.template.placement_mode is ScheduledTicketPlacementMode.backlog
                or sprint_id is not None
            )
            ticket = tickets_actions.create_ticket(
                conn,
                title=schedule.template.title,
                principal=OWNER_PRINCIPAL,
                now=now,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type=schedule.template.worker_type,
                employee_backend=schedule.template.employee_backend,
                employee_launch_model=schedule.template.employee_launch_model,
                kickoff_note=schedule.template.kickoff_note,
                project_id=project_id,
                sprint_id=sprint_id,
                priority=schedule.template.priority,
                deadline=schedule.template.deadline,
                sprint_item_id=sprint_item_id,
                blocked_by_ticket_ids=list(schedule.template.blocked_by_ticket_ids),
                planning_now=planning_now,
                boundary_hour=boundary_hour,
                sprint_item_id_explicit=True,
                sprint_id_explicit=sprint_id_explicit,
            )
            return data.insert_occurrence(
                conn,
                schedule_id=schedule.id,
                occurrence_key=key,
                target_day_id=target_day,
                outcome=OccurrenceOutcome.created,
                ticket_id=ticket.id,
                error=None,
                now=now,
            )
    except Exception as exc:
        _LOG.exception("scheduled Ticket occurrence failed", extra={"schedule_id": schedule.id})
        message = f"{type(exc).__name__}: {exc}"
        try:
            with data.transaction(conn):
                existing_occurrence = data.read_occurrence(conn, schedule.id, key)
                if existing_occurrence is not None:
                    return existing_occurrence
                return data.insert_occurrence(
                    conn,
                    schedule_id=schedule.id,
                    occurrence_key=key,
                    target_day_id=target_day,
                    outcome=OccurrenceOutcome.failed,
                    ticket_id=None,
                    error=message,
                    now=now,
                )
        except Exception:
            _LOG.exception(
                "failed to record scheduled Ticket occurrence failure",
                extra={"schedule_id": schedule.id},
            )
            raise


def run_current_slot(
    conn: sqlite3.Connection,
    *,
    planning_now: datetime,
    now: int,
    boundary_hour: int,
) -> list[ScheduledTicketOccurrence]:
    schedules = data.list_enabled_for_time(conn, current_local_time(planning_now))
    if not schedules:
        return []
    planning_day = planning_day_for(planning_now, boundary_hour)
    sprint_ranges = _sprint_ranges(conn)
    results: list[ScheduledTicketOccurrence] = []
    for schedule in schedules:
        try:
            qualifies = cadence_qualifies(
                schedule.cadence,
                planning_day=planning_day,
                sprint_ranges=sprint_ranges,
            )
        except (StopIteration, ValueError) as exc:
            raise PlannerError(
                ErrorCode.validation,
                "sprint ranges cannot resolve scheduled Ticket cadence",
                {"schedule_id": schedule.id},
            ) from exc
        if qualifies:
            results.append(
                _settle_occurrence(
                    conn,
                    schedule,
                    planning_now=planning_now,
                    now=now,
                    boundary_hour=boundary_hour,
                )
            )
    return results
