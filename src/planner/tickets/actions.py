"""Ticket application actions that coordinate writes with external owners."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import datetime

from planner.core import links as core_links
from planner.core.contracts import LinkKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import resolve_day_id
from planner.runtime.contracts import EmployeeRevisionRunner
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket
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
    return tickets_data.create_ticket(
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


def create_ticket_from_external_work(
    conn: sqlite3.Connection,
    *,
    title: str,
    target_stage: str,
    provided_values: Mapping[str, str],
    actor: str,
    now: int,
    title_max_chars: int,
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
    return tickets_data.create_ticket_from_external_work(
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


def add_link(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    kind: LinkKind,
    *,
    now: int,
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


def remove_link(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    kind: LinkKind,
    *,
    now: int,
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
