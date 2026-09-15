"""Ticket application actions that coordinate writes with external owners."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime

from planner.conversation.contracts import ConversationSystem, PromptDeliveryRefused
from planner.core import links as core_links
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import LinkKind, Principal, PrincipalKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import resolve_day_id
from planner.message_delivery import service as message_delivery_service
from planner.runtime.logic.worker_step_prompt import proposal_returned_for_revision_prompt
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import AtCap, Ticket
from planner.tickets.logic import admission


def proposal_ready_message(ticket_id: str) -> str:
    """A concise wake-up; the holder reads the proposal from canonical Ticket state."""
    return f"Ticket {ticket_id} has filed a proposal for your approval."


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


def add_link(
    conn: sqlite3.Connection,
    from_id: str,
    to_id: str,
    kind: LinkKind,
    *,
    now: int,
    admit: Callable[[], None] | None = None,
) -> None:
    """Create a link and settle the target's blocked stand-in in the same transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if admit is not None:
            admit()
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
    admit: Callable[[], None] | None = None,
) -> None:
    """Delete a link and settle the target's blocked stand-in in the same transaction."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if admit is not None:
            admit()
        core_links.remove_link(conn, from_id, to_id, kind, now)
        if kind is LinkKind.blocks:
            tickets_data.settle_blocked_standin_for_link_target(conn, to_id, now)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


async def file_current_proposal(
    conversation_system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    recap: str,
    ctx: RequestContext,
    clock: Clock,
) -> Ticket:
    """Park a proposal and wake its recorded non-owner holder.

    The Ticket write is canonical and commits first. If delivery is refused the parked
    proposal is deliberately preserved, and the retryable error says so; losing the
    durable proposal would be worse than requiring the caller to retry its wake-up.
    Owner-held proposals use Review and the owner-only notification projection instead.
    """
    ticket = tickets_data.file_current_proposal_with_recap(
        conn,
        ticket_id,
        body=body,
        recap=recap,
        principal=ctx.principal,
        now=clock.now_unix(),
    )
    if ticket.pending_proposal is None or ticket.ceiling_holder.kind is PrincipalKind.owner:
        return ticket
    fate = (
        await message_delivery_service.send_message(
            conversation_system,
            conn,
            clock,
            ctx,
            ticket.ceiling_holder,
            proposal_ready_message(ticket.id),
        )
    ).fate
    if isinstance(fate, PromptDeliveryRefused):
        raise PlannerError(
            ErrorCode.gateway_offline,
            "proposal was parked but its holder could not be notified",
            {
                "ticket_id": ticket.id,
                "proposal_parked": True,
                "holder": {
                    "kind": ticket.ceiling_holder.kind.value,
                    "id": ticket.ceiling_holder.id,
                },
                "refusal_reason": fate.refusal_reason.value,
            },
        )
    return ticket


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
    """Tell the worker its proposal was returned, send the comment, then apply it.

    The order is validate, send, and only then write, because the write is the one thing
    that cannot be undone honestly: the decision deletes the pending proposal, so a revert
    after a failed send would leave nothing to approve. Everything that can be checked
    without changing anything is checked first, against a read of the Ticket.

    A refused delivery changes no Ticket state and is reported as the error it is. The
    rejection lifecycle fact is distinct from the still-pending database transition if
    the comment is refused. A send that succeeds before a later race check can still
    leave a message with an intact proposal; a retry may duplicate it, which is preferable
    to losing the proposal.
    """
    admission.validate_body(message, "revision guidance")
    principal = ctx.principal
    now = clock.now_unix()
    ticket = tickets_data.require_return_for_revision(
        conn,
        ticket_id,
        principal=principal,
        supervisor_sprint_item_id=supervisor_sprint_item_id,
    )
    expected_proposal = ticket.pending_proposal
    lifecycle_fate = (
        await message_delivery_service.send_ticket_system_message(
            conversation_system,
            conn,
            clock,
            ticket_id,
            proposal_returned_for_revision_prompt(),
            required_sprint_item_id=supervisor_sprint_item_id,
        )
    ).fate
    if isinstance(lifecycle_fate, PromptDeliveryRefused):
        raise PlannerError(
            ErrorCode.gateway_offline,
            "proposal return lifecycle could not be delivered",
            {"ticket_id": ticket_id, "refusal_reason": lifecycle_fate.refusal_reason.value},
        )
    current = tickets_data.require_return_for_revision(
        conn,
        ticket_id,
        principal=principal,
        supervisor_sprint_item_id=supervisor_sprint_item_id,
    )
    if current.pending_proposal != expected_proposal:
        raise PlannerError(
            ErrorCode.validation,
            "proposal changed before revision guidance could be delivered",
            {"ticket_id": ticket_id},
        )
    # The lifecycle fact is Panels-authored and arrives first. The decider's comment stays
    # a distinct addressed message with its real sender.
    fate = (
        await message_delivery_service.send_message(
            conversation_system,
            conn,
            clock,
            ctx,
            Principal(PrincipalKind.ticket, ticket_id),
            message.strip(),
            required_sprint_item_id=supervisor_sprint_item_id,
        )
    ).fate
    if isinstance(fate, PromptDeliveryRefused):
        raise PlannerError(
            ErrorCode.gateway_offline,
            "revision guidance could not be delivered",
            {"ticket_id": ticket_id, "refusal_reason": fate.refusal_reason.value},
        )
    return tickets_data.return_for_revision(
        conn,
        ticket_id,
        message=message,
        principal=principal,
        now=now,
        expected_proposal=expected_proposal,
        supervisor_sprint_item_id=supervisor_sprint_item_id,
    )
