"""Ticket application actions that coordinate writes with external owners."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from planner.conversation.contracts import ConversationSystem
from planner.conversation.model_resolution.contracts import CanonicalModelValidation, SelectionKind
from planner.core import ticket_blocks
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.message_delivery import service as message_delivery_service
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket
from planner.tickets.logic import admission

LOGGER = logging.getLogger(__name__)


def create_ticket(
    conn: sqlite3.Connection,
    *,
    title: str,
    principal: Principal,
    now: int,
    title_max_chars: int,
    worker_type: str,
    locale: str | None = None,
    employee_backend: str | None = None,
    employee_launch_model: str | None = None,
    employee_launch_reasoning_effort: str | None = None,
    reasoning_effort_is_set: bool = False,
    model_source_is_historical: bool = False,
    brief: str | None = "",
    project_id: str | None = None,
    deadline: str | None = None,
    manager_id: str | None = None,
    manager_kind: str | None = None,
    blocked_by_ticket_ids: list[str] | None = None,
    stated_ceiling: str | None = None,
    stated_holder: Principal | None = None,
    selection_kind: SelectionKind | str | None = None,
    validation: CanonicalModelValidation | None = None,
) -> Ticket:
    return tickets_data.create_ticket(
        conn,
        title=title,
        principal=principal,
        now=now,
        title_max_chars=title_max_chars,
        brief=brief,
        project_id=(project_id or "project_other") if manager_id is None else project_id,
        deadline=deadline,
        manager_id=manager_id,
        manager_kind=manager_kind,
        worker_type=worker_type,
        locale=locale,
        employee_backend=employee_backend,
        employee_launch_model=employee_launch_model,
        employee_launch_reasoning_effort=employee_launch_reasoning_effort,
        reasoning_effort_is_set=reasoning_effort_is_set,
        model_source_is_historical=model_source_is_historical,
        blocked_by_ticket_ids=blocked_by_ticket_ids,
        stated_ceiling=stated_ceiling,
        stated_holder=stated_holder,
        selection_kind=selection_kind,
        validation=validation,
    )


def add_ticket_block(
    conn: sqlite3.Connection,
    blocking_ticket_id: str,
    blocked_ticket_id: str,
    *,
    now: int,
    admit: Callable[[], None] | None = None,
) -> None:
    """Create a Ticket block under admission, in one transaction.

    Nothing is settled afterwards. What the blocked Ticket shows is derived from this row.
    """
    with tickets_data._txn(conn):
        if admit is not None:
            admit()
        ticket_blocks.add_ticket_block(conn, blocking_ticket_id, blocked_ticket_id, now)


def remove_ticket_block(
    conn: sqlite3.Connection,
    blocking_ticket_id: str,
    blocked_ticket_id: str,
    *,
    now: int,
    admit: Callable[[], None] | None = None,
) -> None:
    """Remove a Ticket block under admission, in one transaction.

    Nothing is settled afterwards. What the blocked Ticket shows is derived from this row.
    """
    with tickets_data._txn(conn):
        if admit is not None:
            admit()
        ticket_blocks.remove_ticket_block(conn, blocking_ticket_id, blocked_ticket_id, now)


def file_current_proposal(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    body: str,
    ctx: RequestContext,
    clock: Clock,
) -> Ticket:
    """A Worker's answer to the current Stage: settled below the ceiling, parked at it.

    The recap does not come with it. The recap is the Ticket's running orientation and a
    Worker keeps it current as it works, which is a different thing from what the Worker
    is asking to have approved.
    """
    return tickets_data.file_current_proposal(
        conn,
        ticket_id,
        body=body,
        principal=ctx.principal,
        now=clock.now_unix(),
    )


async def reject_ticket_proposal(
    conversation_system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    message: str | None,
    ctx: RequestContext,
    clock: Clock,
) -> Ticket:
    """Send the proposal back, with guidance for the executing agent or without."""
    if message is not None:
        admission.validate_revision_guidance(message)
    principal = ctx.principal
    source_turn = await message_delivery_service.revision_source_turn(
        conversation_system, conn, ctx=ctx
    )
    now = clock.now_unix()
    ticket = tickets_data.require_reject(
        conn,
        ticket_id,
        principal=principal,
        has_guidance=message is not None,
    )
    revised = tickets_data.reject_proposal(
        conn,
        ticket_id,
        message=message,
        principal=principal,
        now=now,
        expected_proposal=ticket.pending_proposal,
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
