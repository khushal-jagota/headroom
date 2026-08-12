"""Ticket application actions that coordinate writes with external owners."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Final

from planner.conversation.contracts import ConversationSystem, PromptDeliveryRefused
from planner.conversation.message_content import text_message_content
from planner.core import links as core_links
from planner.core.contracts import LinkKind, Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import resolve_day_id
from planner.runtime.conversation_start import send_to_ticket_conversation
from planner.runtime.logic.worker_step_prompt import revision_guidance_prompt
from planner.sprints import data as sprints_data
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Proposal, Ticket
from planner.tickets.logic import admission, fields_codec, resolution
from planner.worker_context.contracts import WorkerContextService
from planner.worker_types.configuration import configured_worker_type_registry

_log = logging.getLogger(__name__)

OWNER_SENDER_LABEL: Final = "owner"


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
            "SELECT project_id, sprint_id FROM sprint_items WHERE id = ?",
            (sprint_item_id,),
        ).fetchone()
        if item is None:
            return day_id, project_id or "", sprint_id, sprint_item_id
        if project_id is not None and project_id != item["project_id"]:
            raise PlannerError(ErrorCode.validation, "ticket placement does not match sprint item")
        if sprint_id_explicit and sprint_id != item["sprint_id"]:
            raise PlannerError(ErrorCode.validation, "ticket placement does not match sprint item")
        return day_id, str(item["project_id"]), item["sprint_id"], sprint_item_id
    if sprint_id_explicit:
        if sprint_id is not None and worker_type in {
            "planning-day",
            "planning-midday-check",
            "planning-sprint",
        }:
            item = sprints_data.ensure_planning_item(conn, sprint_id=sprint_id, now=0)
            return day_id, item.project_id, sprint_id, item.id
        return day_id, project_id or "project_other", sprint_id, None
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
    if current_id is not None and worker_type in {
        "planning-day",
        "planning-midday-check",
        "planning-sprint",
    }:
        item = sprints_data.ensure_planning_item(conn, sprint_id=current_id, now=0)
        return day_id, item.project_id, current_id, item.id
    return day_id, project_id or "project_other", current_id, None


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    actor: str,
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
            sprint_id_explicit=(
                sprint_id_explicit or (sprint_item_id_explicit and sprint_item_id is None)
            ),
            worker_type=worker_type,
        )
    return tickets_data.create_ticket(
        conn,
        title=title,
        actor=actor,
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
            sprint_id_explicit=(
                sprint_id_explicit or (sprint_item_id_explicit and sprint_item_id is None)
            ),
            worker_type=worker_type,
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


async def return_ticket_for_revision(
    conversation_system: ConversationSystem,
    worker_context_service: WorkerContextService,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str,
    actor: str,
    now: int,
    supervisor_sprint_item_id: str | None = None,
) -> Ticket:
    """Send the owner's guidance to the worker, then hand the Ticket back to it.

    The order is validate, send, and only then write, because the write is the one thing
    that cannot be undone honestly: the decision deletes the pending proposal, so a revert
    after a failed send would leave nothing to approve. Everything that can be checked
    without changing anything is checked first, against a read of the Ticket.

    A refused delivery changes nothing at all and is reported as the error it is. The one
    residue is a send that succeeded and a write that then failed: the guidance is out and
    the proposal is intact, so a retry may deliver the same guidance twice — visible,
    harmless, and far better than losing the proposal.
    """
    admission.validate_body(message, "revision guidance")
    ticket = tickets_data.read_ticket(conn, ticket_id)
    # The decision is the whole check, run here on a read of the Ticket: wrong actor,
    # wrong status, terminal stage, and no conversation to send into all fail here,
    # before a word has been sent and before anything has been written.
    worker_type_definition = configured_worker_type_registry().require(ticket.worker_type)
    resolution.decide_return_for_revision(
        ticket,
        actor,
        worker_type_definition=worker_type_definition,
    )
    field = worker_type_definition.gating_field(ticket.stage)
    expected_proposal: Proposal | None = (
        fields_codec.get_slot(ticket.fields, field).proposal if field is not None else None
    )
    prepared = worker_context_service.prepare(
        ticket_id,
        revision_guidance_prompt(message.strip()),
    )
    # Into the conversation the decision above proved is there: returning for revision is
    # something said to a worker already at work, never the thing that first speaks to one.
    fate = (
        await send_to_ticket_conversation(
            conversation_system,
            conn,
            ticket_id,
            text_message_content(prepared.model_text),
            conversation_id=ticket.conversation_id,
            sender_label=OWNER_SENDER_LABEL,
            now=now,
        )
    ).fate
    if isinstance(fate, PromptDeliveryRefused):
        raise PlannerError(
            ErrorCode.gateway_offline,
            "revision guidance could not be delivered",
            {"ticket_id": ticket_id, "refusal_reason": fate.refusal_reason.value},
        )
    try:
        worker_context_service.acknowledge(ticket_id, prepared.receipts)
    except Exception:
        # The guidance is delivered; failing to tick the context off is reported and
        # otherwise left alone, because nothing here can un-send it.
        _log.exception(
            "delivered worker context could not be acknowledged (ticket=%s)",
            ticket_id,
        )
    return tickets_data.return_for_revision(
        conn,
        ticket_id,
        message=message,
        actor=actor,
        now=now,
        expected_proposal=expected_proposal,
        supervisor_sprint_item_id=supervisor_sprint_item_id,
    )
