"""Move an idle Ticket while excluding conversation and worker-step admission."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from planner.conversation.contracts import (
    ConversationSystem,
    MoveRetirementDisposition,
    MoveSourceRetirement,
)
from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime.conversation_start import conversation_link_lock
from planner.tickets import data
from planner.tickets.contracts import Ticket, TicketLocale


async def move_ticket_locale(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    locale: TicketLocale,
    principal: Principal,
    now: int,
    expected_record_sha256: str | None = None,
    source: MoveSourceRetirement | None = None,
    source_factory: Callable[[Ticket], MoveSourceRetirement] | None = None,
) -> Ticket:
    """Change only locale, with the idle check and canonical write indivisible.

    The link lock excludes new conversations, Ticket sends, resets and worker claims.
    The conversation lock excludes held promotion and already admitted delivery. The
    synchronous writer checks authority, compatibility and the outstanding claim inside
    its own transaction; no backend I/O or await occurs with that transaction open.
    """
    link_lock = conversation_link_lock(f"ticket:{ticket_id}")
    if link_lock.locked():
        raise PlannerError(
            ErrorCode.validation,
            "the Ticket cannot move while a conversation operation is in progress",
            {"ticket_id": ticket_id},
        )
    async with link_lock:
        ticket = data.validate_ticket_locale_move(
            conn,
            ticket_id,
            locale=locale,
            principal=principal,
            expected_record_sha256=expected_record_sha256,
        )
        if ticket.locale is locale:
            return ticket
        if ticket.locale is TicketLocale.macos and source_factory is not None:
            source = source_factory(ticket)
        if ticket.locale is TicketLocale.macos and source is None:
            raise PlannerError(
                ErrorCode.validation,
                "current Mac source cannot be verified",
                {"ticket_id": ticket_id},
            )
        moved: Ticket | None = None

        def write() -> None:
            nonlocal moved
            moved = data.move_ticket_locale(
                conn,
                ticket_id,
                locale=locale,
                principal=principal,
                now=now,
                expected_record_sha256=expected_record_sha256,
                expected_source_locale=ticket.locale,
                expected_conversation_id=ticket.conversation_id,
                validate_source=None if source is None else source.validate_current,
            )

        result = await system.retire_idle_for_move(ticket.conversation_id, write, source=source)
        if result.disposition is not MoveRetirementDisposition.retired:
            raise PlannerError(
                ErrorCode.validation,
                "the Ticket cannot move: "
                + (result.reason or "source retirement was not confirmed"),
                {
                    "ticket_id": ticket_id,
                    "retirement_started": result.disposition is MoveRetirementDisposition.uncertain,
                    "source_retired": None
                    if result.disposition is MoveRetirementDisposition.uncertain
                    else False,
                },
            )
        assert moved is not None
        return moved
