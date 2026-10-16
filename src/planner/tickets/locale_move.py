"""Move an idle Ticket while excluding conversation and worker-step admission."""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import ConversationSystem
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
        ticket = data.read_ticket(conn, ticket_id)
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
            )

        if ticket.conversation_id is None:
            write()
        elif not await system.run_if_idle(ticket.conversation_id, write):
            raise PlannerError(
                ErrorCode.validation,
                "the Ticket cannot move while its conversation has activity or waiting messages",
                {"ticket_id": ticket_id},
            )
        assert moved is not None
        return moved
