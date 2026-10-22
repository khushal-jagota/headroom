"""Application actions for schedule management and exact-slot execution."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import cast

from planner.core import authority
from planner.core.contracts import OWNER_PRINCIPAL, Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.scheduled_tickets import data
from planner.scheduled_tickets.contracts import (
    OccurrenceOutcome,
    ScheduleCadence,
    ScheduledTicketOccurrence,
    ScheduledTicketSchedule,
    ScheduledTicketTemplate,
)
from planner.scheduled_tickets.logic import (
    cadence_qualifies,
    current_local_time,
    occurrence_key,
    validate_local_time,
)
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.logic.admission import require_creatable_worker_type
from planner.tickets.logic.locale import require_ticket_locale
from planner.worker_types.contracts import RETIRED_WORKER_TYPES

_LOG = logging.getLogger(__name__)


def require_schedule_mutation(
    schedule: ScheduledTicketSchedule, changes: Mapping[str, object]
) -> None:
    """Historical schedules can only receive an explicit disable operation."""
    retired = (
        schedule.cadence is not ScheduleCadence.daily
        or schedule.template.worker_type in RETIRED_WORKER_TYPES
    )
    if retired and not (set(changes) == {"enabled"} and changes["enabled"] is False):
        raise PlannerError(ErrorCode.validation, "retired schedules can only be disabled")


def _validate_template(
    conn: sqlite3.Connection,
    template: ScheduledTicketTemplate,
    *,
    model_source_is_historical: bool = False,
) -> ScheduledTicketTemplate:
    require_creatable_worker_type(template.worker_type)
    if template.manager_kind not in {"chief", "manager"} or (
        (template.manager_kind == "manager") != (template.manager_id is not None)
    ):
        raise PlannerError(
            ErrorCode.validation, "schedule requires an explicit manager assignment"
        )
    resolved = tickets_data.resolve_ticket_creation_context(
        conn,
        locale=template.locale,
        title=template.title,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type=template.worker_type,
        employee_backend=template.employee_backend,
        employee_launch_model=template.employee_launch_model,
        model_source_is_historical=model_source_is_historical,
        project_id=template.project_id,
        deadline=template.deadline,
        manager_id=template.manager_id,
        blocked_by_ticket_ids=list(template.blocked_by_ticket_ids),
    )
    return replace(template, locale=resolved.locale, project_id=resolved.project_id)


def create_schedule(
    conn: sqlite3.Connection,
    *,
    principal: Principal,
    enabled: bool,
    cadence: ScheduleCadence,
    local_time: str,
    template: ScheduledTicketTemplate,
    now: int,
) -> ScheduledTicketSchedule:
    authority.require_above(conn, principal, authority.owner_only("scheduled_ticket_schedules"))
    if cadence is not ScheduleCadence.daily:
        raise PlannerError(ErrorCode.validation, "calendar planning cadences are retired")
    with data.transaction(conn):
        if template.manager_id is not None and template.project_id is None:
            item = conn.execute(
                "SELECT project_id FROM managers WHERE id = ?",
                (template.manager_id,),
            ).fetchone()
            if item is not None:
                template = replace(
                    template,
                    project_id=str(item["project_id"]),
                )
        template = _validate_template(conn, template)
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
    principal: Principal,
    now: int,
) -> ScheduledTicketSchedule:
    authority.require_above(conn, principal, authority.owner_only("scheduled_ticket_schedules"))
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
    if (
        "cadence" in changes
        and ScheduleCadence(str(changes["cadence"])) is not ScheduleCadence.daily
    ):
        raise PlannerError(ErrorCode.validation, "calendar planning cadences are retired")
    require_schedule_mutation(current, changes)
    if set(changes) <= {"enabled"} and changes.get("enabled") is False:
        return data.update_schedule(conn, schedule_id, changes, now=now)
    item_id = normalized_changes.get("manager_id", current.template.manager_id)
    if item_id is not None:
        item = conn.execute("SELECT project_id FROM managers WHERE id=?", (item_id,)).fetchone()
        if item is not None:
            if (
                "project_id" in normalized_changes
                and normalized_changes["project_id"] != item["project_id"]
            ):
                raise PlannerError(ErrorCode.validation, "schedule Project does not match Outcome")
            normalized_changes["project_id"] = str(item["project_id"])

    template_values: dict[str, object] = {
        "locale": current.template.locale,
        "title": current.template.title,
        "worker_type": current.template.worker_type,
        "kickoff_note": current.template.kickoff_note,
        "deadline": current.template.deadline,
        "project_id": current.template.project_id,
        "manager_id": current.template.manager_id,
        "manager_kind": current.template.manager_kind,
        "employee_backend": current.template.employee_backend,
        "employee_launch_model": current.template.employee_launch_model,
        "blocked_by_ticket_ids": current.template.blocked_by_ticket_ids,
    }
    for key in tuple(template_values):
        if key in normalized_changes:
            template_values[key] = normalized_changes[key]
    template = ScheduledTicketTemplate(
        locale=(
            None
            if template_values["locale"] is None
            else require_ticket_locale(template_values["locale"])
        ),
        manager_kind=str(template_values["manager_kind"]),
        title=str(template_values["title"]),
        worker_type=str(template_values["worker_type"]),
        kickoff_note=str(template_values["kickoff_note"]),
        deadline=(
            None if template_values["deadline"] is None else str(template_values["deadline"])
        ),
        project_id=(
            None if template_values["project_id"] is None else str(template_values["project_id"])
        ),
        manager_id=(
            None
            if template_values["manager_id"] is None
            else str(template_values["manager_id"])
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
    template = _validate_template(
        conn,
        template,
        model_source_is_historical=not (
            {"employee_backend", "employee_launch_model"} & changes.keys()
        ),
    )
    encoded = normalized_changes
    encoded["locale"] = template.locale
    if "local_time" in encoded:
        encoded["local_time"] = validate_local_time(str(encoded["local_time"]))
    return data.update_schedule(conn, schedule_id, encoded, now=now)


def _settle_occurrence(
    conn: sqlite3.Connection,
    schedule: ScheduledTicketSchedule,
    *,
    local_now: datetime,
    now: int,
) -> ScheduledTicketOccurrence:
    key = occurrence_key(local_now)
    target_date = local_now.date().isoformat()
    try:
        with data.transaction(conn):
            existing_occurrence = data.read_occurrence(conn, schedule.id, key)
            if existing_occurrence is not None:
                return existing_occurrence
            manager_id = schedule.template.manager_id
            project_id = schedule.template.project_id
            # Due execution is trusted scheduler work over stored configuration.
            # Mutation admission is owner-or-Chief only. It does not certify historical authorship.
            # OWNER_PRINCIPAL preserves ordinary Ticket creation and approval gates.
            ticket = tickets_actions.create_ticket(
                conn,
                locale=schedule.template.locale,
                title=schedule.template.title,
                principal=OWNER_PRINCIPAL,
                now=now,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type=schedule.template.worker_type,
                employee_backend=schedule.template.employee_backend,
                employee_launch_model=schedule.template.employee_launch_model,
                model_source_is_historical=True,
                brief=schedule.template.kickoff_note,
                project_id=project_id,
                deadline=schedule.template.deadline,
                manager_id=manager_id,
                blocked_by_ticket_ids=list(schedule.template.blocked_by_ticket_ids),
                manager_kind=schedule.template.manager_kind,
            )
            return data.insert_occurrence(
                conn,
                schedule_id=schedule.id,
                occurrence_key=key,
                target_date=target_date,
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
                    target_date=target_date,
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
    local_now: datetime,
    now: int,
) -> list[ScheduledTicketOccurrence]:
    schedules = data.list_enabled_for_time(conn, current_local_time(local_now))
    if not schedules:
        return []
    results: list[ScheduledTicketOccurrence] = []
    for schedule in schedules:
        if schedule.template.worker_type in RETIRED_WORKER_TYPES:
            continue
        if cadence_qualifies(schedule.cadence):
            results.append(_settle_occurrence(conn, schedule, local_now=local_now, now=now))
    return results
