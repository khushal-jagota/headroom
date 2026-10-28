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
    from planner.tickets.launch_authority import (
        acquire_ticket_mutation,
        launch_authority_scope,
    )

    permit = acquire_ticket_mutation(conn, ticket_id)
    try:
        with launch_authority_scope(permit):
            return await _move_ticket_locale_with_authority(
                system,
                conn,
                ticket_id,
                locale=locale,
                principal=principal,
                now=now,
                expected_record_sha256=expected_record_sha256,
                source=source,
                source_factory=source_factory,
            )
    finally:
        permit.release(conn)


async def _move_ticket_locale_with_authority(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    locale: TicketLocale,
    principal: Principal,
    now: int,
    expected_record_sha256: str | None,
    source: MoveSourceRetirement | None,
    source_factory: Callable[[Ticket], MoveSourceRetirement] | None,
) -> Ticket:
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
        from planner.runtime.conversation_start import (
            refuse_known_incompatibility,
            verify_ticket_launch,
        )

        validation = None
        if ticket.employee_backend != "hermes" and ticket.employee_launch_model is not None:
            validation = await verify_ticket_launch(
                system,
                conn,
                ticket,
                locale=locale,
                persist=False,
                now=now,
            )
            refuse_known_incompatibility(validation)
        # Recheck authority and exact source before any retirement after discovery.
        current = data.validate_ticket_locale_move(
            conn,
            ticket_id,
            locale=locale,
            principal=principal,
            expected_record_sha256=expected_record_sha256,
        )
        if current != ticket:
            raise PlannerError(
                ErrorCode.validation, "Ticket changed during destination verification", {}
            )
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
                validation=validation,
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
