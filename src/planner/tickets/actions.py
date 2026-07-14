"""Ticket application actions that coordinate writes with external owners."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace

from planner.core.contracts import Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime.contracts import EmployeeRevisionRunner
from planner.runtime.readiness_doorbell import ReadinessDoorbell
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    AtCap,
    NextCeiling,
    Ticket,
    TicketDeletion,
)
from planner.tickets.logic import admission


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    actor: str,
    now: int,
    title_max_chars: int,
    readiness_doorbell: ReadinessDoorbell,
    worker_type: str,
    kickoff_note: str = "",
    project_id: str | None = None,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
) -> Ticket:
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
        sprint_id=sprint_id,
        sprint_item_id=sprint_item_id,
        worker_type=worker_type,
    )
    readiness_doorbell.ring()
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
    readiness_doorbell: ReadinessDoorbell,
    worker_type: str,
    kickoff_note: str | None = None,
    recap: str | None = None,
    project_id: str | None = None,
    priority: Priority = Priority.P3,
    deadline: str | None = None,
    sprint_id: str | None = None,
    sprint_item_id: str | None = None,
) -> Ticket:
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
        sprint_id=sprint_id,
        sprint_item_id=sprint_item_id,
        worker_type=worker_type,
    )
    readiness_doorbell.ring()
    return ticket


def reconcile_ticket_from_external_work(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    target_stage: str,
    provided_values: Mapping[str, str],
    actor: str,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
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
        readiness_doorbell.ring()
    return ticket


def delete_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    actor: str,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
) -> TicketDeletion:
    deleted = tickets_data.delete_ticket(conn, ticket_id, actor=actor, now=now)
    readiness_doorbell.ring()
    return deleted


def accept_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    actor: str,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
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
    readiness_doorbell.ring()
    return ticket


def edit_field_value(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    field: str,
    new_body: str,
    actor: str,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
) -> Ticket:
    ticket = tickets_data.edit_field_value(
        conn,
        ticket_id,
        field=field,
        new_body=new_body,
        actor=actor,
        now=now,
    )
    readiness_doorbell.ring()
    return ticket


def change_scope(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    ceiling: str,
    at_cap: AtCap,
    actor: str,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
) -> Ticket:
    ticket = tickets_data.change_scope(
        conn,
        ticket_id,
        ceiling=ceiling,
        at_cap=at_cap,
        actor=actor,
        now=now,
    )
    readiness_doorbell.ring()
    return ticket


def set_stage(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    new_stage: str,
    actor: str,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
) -> Ticket:
    ticket = tickets_data.set_stage(
        conn,
        ticket_id,
        new_stage=new_stage,
        actor=actor,
        now=now,
    )
    readiness_doorbell.ring()
    return ticket


def drop_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    actor: str,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
) -> Ticket:
    ticket = tickets_data.drop_ticket(conn, ticket_id, actor=actor, now=now)
    readiness_doorbell.ring()
    return ticket


def take_over_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
) -> Ticket:
    ticket = tickets_data.take_over_ticket(conn, ticket_id, now=now)
    readiness_doorbell.ring()
    return ticket


def release_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    now: int,
    readiness_doorbell: ReadinessDoorbell,
) -> Ticket:
    ticket = tickets_data.release_ticket(conn, ticket_id, now=now)
    readiness_doorbell.ring()
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
