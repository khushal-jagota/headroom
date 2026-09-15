"""Recover and deliver durable proposal-holder wakes."""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryQueued,
    PromptDeliveryRefused,
)
from planner.core.clock import Clock
from planner.message_delivery import service as message_delivery_service
from planner.proposal_holder_wakes import data
from planner.tickets import data as tickets_data


async def deliver_pending_wakes(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    *,
    ticket_id: str | None = None,
    retry_delay_seconds: int = 1,
) -> int:
    """Attempt due wakes; deterministic message ids make every replay idempotent."""
    now = clock.now_unix()
    data.reconcile_missing(conn, now=now)
    delivered_count = 0
    for wake in data.due(conn, now=now, ticket_id=ticket_id):
        ticket = tickets_data.read_ticket(conn, wake.ticket_id)
        if ticket.pending_proposal is None or ticket.ceiling_holder != wake.holder:
            data.cancel(conn, wake.ticket_id, now=now)
            continue
        try:
            result = await message_delivery_service.send_system_message(
                conversations,
                conn,
                clock,
                wake.holder,
                wake.message,
                sender_message_id=wake.sender_message_id,
            )
        except Exception as error:
            data.record_exception(
                conn,
                wake,
                error=str(error),
                retry_at=now + retry_delay_seconds,
                now=now,
            )
            continue
        if isinstance(result.fate, PromptDeliveryRefused):
            data.record_refusal(
                conn,
                wake,
                error=result.fate.refusal_reason.value,
                retry_at=now + retry_delay_seconds,
                now=now,
            )
        elif isinstance(result.fate, PromptDeliveryQueued):
            # A queue is process-local. Keep the same attempt pending until its durable
            # prompt outcome can be discovered, or a restart safely resends it.
            continue
        elif data.mark_delivered(conn, wake, now=now):
            delivered_count += 1
    return delivered_count
