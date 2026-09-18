"""Ticket application actions that coordinate writes with external owners."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime

from planner.conversation.contracts import ConversationSystem
from planner.core import ticket_blocks
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import resolve_day_id
from planner.message_delivery import service as message_delivery_service
from planner.runtime.logic.worker_step_prompt import proposal_returned_for_revision_prompt
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, Ticket
from planner.tickets.logic import admission

LOGGER = logging.getLogger(__name__)


def resolve_creation_placement(
    conn: sqlite3.Connection,
    *,
    planning_now: datetime,
    boundary_hour: int,
    sprint_item_id: str | None,
    project_id: str | None,
    sprint_id: str | None,
    sprint_id_explicit: bool,
    worker_type: str,
) -> tuple[str, str, str | None, str | None]:
    """Resolve defaults for a newly created Ticket without overriding placement intent."""
    day_id = resolve_day_id("today", planning_now, boundary_hour)
    if sprint_item_id is not None:
        item = conn.execute(
            "SELECT project_id FROM sprint_items WHERE id = ?", (sprint_item_id,)
        ).fetchone()
        if item is not None:
            if project_id is not None and project_id != item["project_id"]:
                raise PlannerError(ErrorCode.validation, "ticket Project does not match Outcome")
            project_id = str(item["project_id"])
    if sprint_id_explicit:
        return day_id, project_id or "project_other", sprint_id, sprint_item_id
    planning_day = day_id.removeprefix("day_")
    ranges = [
        DateRange(
            id=str(row["id"]),
            date_start=str(row["date_start"]),
            date_end=str(row["date_end"]),
        )
        for row in conn.execute("SELECT id, date_start, date_end FROM sprints").fetchall()
    ]
    current_id = current_sprint_id(planning_day, ranges)
    return day_id, project_id or "project_other", current_id, sprint_item_id


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    principal: Principal,
    now: int,
    title_max_chars: int,
    worker_type: str,
    employee_backend: str | None = None,
    employee_launch_model: str | None = None,
    kickoff_note: str | None = "",
    project_id: str | None = None,
    sprint_id: str | None = None,
    priority: Priority | None = None,
    deadline: str | None = None,
    sprint_item_id: str | None = None,
    blocked_by_ticket_ids: list[str] | None = None,
    planning_now: datetime | None = None,
    boundary_hour: int = 5,
    sprint_item_id_explicit: bool = False,
    sprint_id_explicit: bool = False,
    stated_ceiling: str | None = None,
    stated_at_cap: AtCap | None = None,
) -> Ticket:
    if planning_now is None:
        day_id = None
    else:
        day_id, project_id, sprint_id, sprint_item_id = resolve_creation_placement(
            conn,
            planning_now=planning_now,
            boundary_hour=boundary_hour,
            sprint_item_id=sprint_item_id,
            project_id=project_id,
            sprint_id=sprint_id,
            sprint_id_explicit=sprint_id_explicit,
            worker_type=worker_type,
        )
    return tickets_data.create_ticket(
        conn,
        title=title,
        principal=principal,
        now=now,
        title_max_chars=title_max_chars,
        kickoff_note=kickoff_note,
        project_id=project_id,
        sprint_id=sprint_id,
        priority=priority,
        deadline=deadline,
        sprint_item_id=sprint_item_id,
        day_id=day_id,
        worker_type=worker_type,
        employee_backend=employee_backend,
        employee_launch_model=employee_launch_model,
        blocked_by_ticket_ids=blocked_by_ticket_ids,
        stated_ceiling=stated_ceiling,
        stated_at_cap=stated_at_cap,
    )


def create_ticket_from_external_work(
    conn: sqlite3.Connection,
    *,
    title: str,
    target_stage: str,
    provided_values: Mapping[str, str],
    principal: Principal,
    now: int,
    title_max_chars: int,
    worker_type: str,
    employee_backend: str | None = None,
    employee_launch_model: str | None = None,
    kickoff_note: str | None = None,
    recap: str | None = None,
    project_id: str | None = None,
    sprint_id: str | None = None,
    priority: Priority | None = None,
    deadline: str | None = None,
    sprint_item_id: str | None = None,
    blocked_by_ticket_ids: list[str] | None = None,
    planning_now: datetime | None = None,
    boundary_hour: int = 5,
    sprint_item_id_explicit: bool = False,
    sprint_id_explicit: bool = False,
) -> Ticket:
    if planning_now is None:
        day_id = None
    else:
        day_id, project_id, sprint_id, sprint_item_id = resolve_creation_placement(
            conn,
            planning_now=planning_now,
            boundary_hour=boundary_hour,
            sprint_item_id=sprint_item_id,
            project_id=project_id,
            sprint_id=sprint_id,
            sprint_id_explicit=sprint_id_explicit,
            worker_type=worker_type,
        )
    return tickets_data.create_ticket_from_external_work(
        conn,
        title=title,
        kickoff_note=kickoff_note,
        target_stage=target_stage,
        provided_values=provided_values,
        principal=principal,
        now=now,
        title_max_chars=title_max_chars,
        recap=recap,
        project_id=project_id,
        sprint_id=sprint_id,
        priority=priority,
        deadline=deadline,
        sprint_item_id=sprint_item_id,
        day_id=day_id,
        worker_type=worker_type,
        employee_backend=employee_backend,
        employee_launch_model=employee_launch_model,
        blocked_by_ticket_ids=blocked_by_ticket_ids,
    )


def add_ticket_block(
    conn: sqlite3.Connection,
    blocking_ticket_id: str,
    blocked_ticket_id: str,
    *,
    now: int,
    admit: Callable[[], None] | None = None,
) -> None:
    """Create a Ticket block and settle the blocked Ticket in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if admit is not None:
            admit()
        ticket_blocks.add_ticket_block(conn, blocking_ticket_id, blocked_ticket_id, now)
        tickets_data.settle_blocked_standin_for_ticket(conn, blocked_ticket_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def remove_ticket_block(
    conn: sqlite3.Connection,
    blocking_ticket_id: str,
    blocked_ticket_id: str,
    *,
    now: int,
    admit: Callable[[], None] | None = None,
) -> None:
    """Remove a Ticket block and settle the blocked Ticket in one transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if admit is not None:
            admit()
        ticket_blocks.remove_ticket_block(conn, blocking_ticket_id, blocked_ticket_id, now)
        tickets_data.settle_blocked_standin_for_ticket(conn, blocked_ticket_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def file_current_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    recap: str,
    ctx: RequestContext,
    clock: Clock,
) -> Ticket:
    """Park a proposal and its wake intent; the machine-lock loop delivers it."""
    return tickets_data.file_current_proposal_with_recap(
        conn,
        ticket_id,
        body=body,
        recap=recap,
        principal=ctx.principal,
        now=clock.now_unix(),
    )


async def return_ticket_for_revision(
    conversation_system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str,
    ctx: RequestContext,
    clock: Clock,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    """Commit the rejection and two durable messages, then return without backend I/O."""
    admission.validate_body(message, "revision guidance")
    principal = ctx.principal
    now = clock.now_unix()
    source_turn = await message_delivery_service.revision_source_turn(
        conversation_system, conn, ctx=ctx
    )
    ticket = tickets_data.require_return_for_revision(
        conn,
        ticket_id,
        principal=principal,
        supervisor_sprint_item_id=supervisor_sprint_item_id,
    )
    revised = tickets_data.return_for_revision(
        conn,
        ticket_id,
        message=message,
        lifecycle_message=proposal_returned_for_revision_prompt(),
        principal=principal,
        now=now,
        expected_proposal=ticket.pending_proposal,
        supervisor_sprint_item_id=supervisor_sprint_item_id,
    )
    if source_turn is not None:
        try:
            await conversation_system.record_explicit_reply(
                source_turn, Principal(PrincipalKind.ticket, ticket_id)
            )
        except Exception:
            LOGGER.warning(
                "could not record explicit reply for Ticket revision %s",
                ticket_id,
                exc_info=True,
            )
    return revised
