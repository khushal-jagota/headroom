"""Ticket application actions that coordinate writes with external owners."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime

from planner.core import links as core_links
from planner.core.contracts import LinkKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import resolve_day_id
from planner.runtime.automatic_employee_step_eligibility_wake import (
    AutomaticEmployeeStepEligibilityWake,
)
from planner.runtime.contracts import EmployeeRevisionRunner
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    AtCap,
    NextCeiling,
    StageOwnershipMode,
    Ticket,
    TicketDeletion,
)
from planner.tickets.logic import admission


def resolve_creation_placement(
    conn: sqlite3.Connection,
    *,
    planning_now: datetime,
    boundary_hour: int,
    sprint_id: str | None,
    sprint_item_id: str | None,
    sprint_id_explicit: bool = False,
) -> tuple[str, str | None]:
    """Resolve defaults for a newly created Ticket without overriding placement intent."""
    day_id = resolve_day_id("today", planning_now, boundary_hour)
    if sprint_item_id is not None or sprint_id_explicit or sprint_id is not None:
        return day_id, sprint_id
    planning_day = day_id.removeprefix("day_")
    ranges = [
        DateRange(
            id=str(row["id"]),
            date_start=str(row["date_start"]),
            date_end=str(row["date_end"]),
        )
        for row in conn.execute("SELECT id, date_start, date_end FROM sprints").fetchall()
    ]
    return day_id, current_sprint_id(planning_day, ranges)


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    actor: str,
    now: int,
    title_max_chars: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
    worker_type: str,
    employee_backend: str | None = None,
    kickoff_note: str = "",
    project_id: str | None = None,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
    blocked_by_ticket_ids: list[str] | None = None,
    planning_now: datetime | None = None,
    boundary_hour: int = 5,
    sprint_id_explicit: bool = False,
) -> Ticket:
    if planning_now is None:
        day_id, resolved_sprint_id = None, sprint_id
    else:
        day_id, resolved_sprint_id = resolve_creation_placement(
            conn,
            planning_now=planning_now,
            boundary_hour=boundary_hour,
            sprint_id=sprint_id,
            sprint_item_id=sprint_item_id,
            sprint_id_explicit=sprint_id_explicit,
        )
    ticket = tickets_data.create_ticket(
        conn,
        title=title,
        actor=actor,
        now=now,
        title_max_chars=title_max_chars,
        kickoff_note=kickoff_note,
        project_id=project_id,
        priority=priority,
        deadline=deadline,
        sprint_id=resolved_sprint_id,
        sprint_item_id=sprint_item_id,
        day_id=day_id,
        worker_type=worker_type,
        employee_backend=employee_backend,
        blocked_by_ticket_ids=blocked_by_ticket_ids,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def create_ticket_from_external_work(
    conn: sqlite3.Connection,
    *,
    title: str,
    target_stage: str,
    provided_values: Mapping[str, str],
    actor: str,
    now: int,
    title_max_chars: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
    worker_type: str,
    employee_backend: str | None = None,
    kickoff_note: str | None = None,
    recap: str | None = None,
    project_id: str | None = None,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
    blocked_by_ticket_ids: list[str] | None = None,
    planning_now: datetime | None = None,
    boundary_hour: int = 5,
    sprint_id_explicit: bool = False,
) -> Ticket:
    if planning_now is None:
        day_id, resolved_sprint_id = None, sprint_id
    else:
        day_id, resolved_sprint_id = resolve_creation_placement(
            conn,
            planning_now=planning_now,
            boundary_hour=boundary_hour,
            sprint_id=sprint_id,
            sprint_item_id=sprint_item_id,
            sprint_id_explicit=sprint_id_explicit,
        )
    ticket = tickets_data.create_ticket_from_external_work(
        conn,
        title=title,
        kickoff_note=kickoff_note,
        target_stage=target_stage,
        provided_values=provided_values,
        actor=actor,
        now=now,
        title_max_chars=title_max_chars,
        recap=recap,
        project_id=project_id,
        priority=priority,
        deadline=deadline,
        sprint_id=resolved_sprint_id,
        sprint_item_id=sprint_item_id,
        day_id=day_id,
        worker_type=worker_type,
        employee_backend=employee_backend,
        blocked_by_ticket_ids=blocked_by_ticket_ids,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def reconcile_ticket_from_external_work(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    target_stage: str,
    provided_values: Mapping[str, str],
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
    kickoff_note: str | None = None,
    recap: str | None = None,
) -> Ticket:
    before = tickets_data.read_ticket(conn, ticket_id)
    ticket = tickets_data.reconcile_ticket_from_external_work(
        conn,
        ticket_id,
        kickoff_note=kickoff_note,
        target_stage=target_stage,
        provided_values=provided_values,
        actor=actor,
        now=now,
        recap=recap,
    )
    if replace(before, updated_at=ticket.updated_at) != ticket:
        automatic_employee_step_eligibility_wake.wake()
    return ticket


def delete_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> TicketDeletion:
    deleted = tickets_data.delete_ticket(conn, ticket_id, actor=actor, now=now)
    automatic_employee_step_eligibility_wake.wake()
    return deleted


def add_link(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    kind: LinkKind,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> None:
    """Create a link and settle the target's blocked stand-in in the same transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        core_links.add_link(conn, from_id, to_id, kind, now)
        if kind is LinkKind.blocks:
            tickets_data.settle_blocked_standin_for_link_target(conn, to_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    if kind is LinkKind.blocks:
        automatic_employee_step_eligibility_wake.wake()


def remove_link(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    kind: LinkKind,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> None:
    """Delete a link and settle the target's blocked stand-in in the same transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        core_links.remove_link(conn, from_id, to_id, kind, now)
        if kind is LinkKind.blocks:
            tickets_data.settle_blocked_standin_for_link_target(conn, to_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
    if kind is LinkKind.blocks:
        automatic_employee_step_eligibility_wake.wake()


def file_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    body: str,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    ticket = tickets_data.file_proposal(
        conn,
        ticket_id,
        field=field,
        body=body,
        actor=actor,
        now=now,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def file_current_proposal_with_recap(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    recap: str,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    ticket = tickets_data.file_current_proposal_with_recap(
        conn,
        ticket_id,
        body=body,
        recap=recap,
        actor=actor,
        now=now,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def accept_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
    edited_body: str | None = None,
    next_ceiling: NextCeiling | None = None,
    at_cap: AtCap | None = None,
) -> Ticket:
    ticket = tickets_data.accept_proposal(
        conn,
        ticket_id,
        field=field,
        actor=actor,
        now=now,
        edited_body=edited_body,
        next_ceiling=next_ceiling,
        at_cap=at_cap,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def edit_field_value(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    new_body: str,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    ticket = tickets_data.edit_field_value(
        conn,
        ticket_id,
        field=field,
        new_body=new_body,
        actor=actor,
        now=now,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def change_scope(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    ceiling: str,
    at_cap: AtCap,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    ticket = tickets_data.change_scope(
        conn,
        ticket_id,
        ceiling=ceiling,
        at_cap=at_cap,
        actor=actor,
        now=now,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def set_stage(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    new_stage: str,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    ticket = tickets_data.set_stage(
        conn,
        ticket_id,
        new_stage=new_stage,
        actor=actor,
        now=now,
    )
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def drop_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    ticket = tickets_data.drop_ticket(conn, ticket_id, actor=actor, now=now)
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def take_over_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    before = tickets_data.read_ticket(conn, ticket_id)
    ticket = tickets_data.take_over_ticket(conn, ticket_id, now=now)
    if replace(before, updated_at=ticket.updated_at) != ticket:
        automatic_employee_step_eligibility_wake.wake()
    return ticket


def release_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    before = tickets_data.read_ticket(conn, ticket_id)
    ticket = tickets_data.release_ticket(conn, ticket_id, now=now)
    if replace(before, updated_at=ticket.updated_at) != ticket:
        automatic_employee_step_eligibility_wake.wake()
    return ticket


def request_user_help(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    actor: str,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    ticket = tickets_data.request_user_help(conn, ticket_id, actor=actor, now=now)
    automatic_employee_step_eligibility_wake.wake()
    return ticket


def set_stage_ownership(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    stage: str,
    ownership_mode: StageOwnershipMode | None,
    now: int,
    automatic_employee_step_eligibility_wake: AutomaticEmployeeStepEligibilityWake,
) -> Ticket:
    before = tickets_data.read_ticket(conn, ticket_id)
    ticket = tickets_data.set_stage_ownership(
        conn,
        ticket_id,
        stage=stage,
        ownership_mode=ownership_mode,
        now=now,
    )
    if replace(before, updated_at=ticket.updated_at) != ticket:
        automatic_employee_step_eligibility_wake.wake()
    return ticket


def return_ticket_for_revision(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str,
    actor: str,
    now: int,
    employee_revision_runner: EmployeeRevisionRunner | None,
) -> Ticket:
    """Accept a revision handoff before changing the canonical Ticket."""
    admission.validate_body(message, "revision guidance")
    if employee_revision_runner is None:
        raise PlannerError(
            ErrorCode.gateway_offline,
            "employee runner is unavailable",
            {"ticket_id": ticket_id},
        )
    handoff = employee_revision_runner.reserve_revision(ticket_id, message.strip())
    try:
        ticket = tickets_data.return_for_revision(
            conn,
            ticket_id,
            message=message,
            actor=actor,
            now=now,
        )
    except BaseException:
        handoff.cancel()
        raise
    handoff.release()
    return ticket
