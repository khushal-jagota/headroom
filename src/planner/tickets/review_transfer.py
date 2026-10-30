"""One best-effort courtesy notice after a parked review changes hands."""

from __future__ import annotations

import logging
import sqlite3

from planner.conversation.contracts import (
    ConversationSystem,
    PromptDeliveryMode,
    PromptDeliveryRefused,
    PromptDeliverySuppressed,
    PromptDeliveryUncertain,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.managers import data as managers_data
from planner.managers import service as managers_service
from planner.runtime import conversation_start
from planner.tickets import data as tickets_data
from planner.tickets.contracts import ReviewTransferNotice
from planner.worker_settings.service import CHIEF_SETTINGS_KEY

_LOG = logging.getLogger(__name__)


async def notify_review_transfers(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    notices: list[ReviewTransferNotice],
    *,
    now: int,
) -> None:
    """Do not retry notices or fail a successful transfer when delivery fails."""
    for notice in notices:
        try:
            await _notify_review_transfer(system, conn, notice, now=now)
        except Exception:
            _LOG.exception("review transfer notice failed (ticket=%s)", notice.ticket_id)


async def _notify_review_transfer(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    notice: ReviewTransferNotice,
    *,
    now: int,
) -> None:
    new_holder_text = (
        f"{notice.mover_name} moved the review of {notice.ticket_title} to you."
        if notice.mover_name is not None
        else f"You now hold the review of {notice.ticket_title}."
    )
    await _deliver(
        system,
        conn,
        notice.ticket_id,
        notice.new_holder,
        text_message_content(new_holder_text),
        now=now,
    )
    if notice.notify_previous_holder:
        await _deliver(
            system,
            conn,
            notice.ticket_id,
            notice.previous_holder,
            text_message_content(
                f"{notice.mover_name} moved the review of {notice.ticket_title} "
                f"to {notice.new_holder_name}."
            ),
            now=now,
        )


async def _deliver(
    system: ConversationSystem,
    conn: sqlite3.Connection,
    ticket_id: str,
    target: Principal,
    content: MessageContent,
    *,
    now: int,
) -> None:
    if target.kind is PrincipalKind.owner:
        async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
            ticket = tickets_data.read_ticket(conn, ticket_id)
            current = ticket.conversation_id
            if current is None:
                linked = await conversation_start.start_ticket_conversation(
                    system,
                    conn,
                    ticket,
                    conversation_start.worker_resolve(conn, ticket),
                    conversation_id=conversation_start.new_conversation_id(),
                    now=now,
                )
                current = linked.conversation_id
            await system.record_message_to_owner(
                current,
                content,
                sender_label="Panels",
                sender=Principal(PrincipalKind.ticket, ticket_id),
                recipient=OWNER_PRINCIPAL,
                credit_explicit_reply=False,
            )
        return
    if target.kind is PrincipalKind.ticket:
        async with conversation_start.conversation_link_lock(f"ticket:{target.id}"):
            ticket = tickets_data.read_ticket(conn, target.id)
            delivered = await conversation_start.send_to_ticket_conversation(
                system,
                conn,
                target.id,
                content,
                conversation_id=ticket.conversation_id,
                sender_label="Panels",
                mode=PromptDeliveryMode.queue,
                reply_requested=False,
                now=now,
            )
    elif target.kind is PrincipalKind.manager:
        async with managers_service.manager_lifecycle_lock(target.id):
            item = managers_data.read_manager(conn, target.id).item
            async with conversation_start.conversation_link_lock(
                f"agent:{item.manager_agent_key}"
            ):
                delivered = await conversation_start.send_to_agent_conversation(
                    system,
                    conn,
                    item.manager_agent_key,
                    content,
                    conversation_start.manager_resolve(conn, item),
                    conversation_id=conversation_start.read_agent_conversation(
                        conn, item.manager_agent_key
                    ),
                    sender_label="Panels",
                    mode=PromptDeliveryMode.queue,
                    reply_requested=False,
                    required_manager_id=item.id,
                )
    else:
        async with conversation_start.conversation_link_lock(f"agent:{CHIEF_SETTINGS_KEY}"):
            delivered = await conversation_start.send_to_agent_conversation(
                system,
                conn,
                CHIEF_SETTINGS_KEY,
                content,
                conversation_start.agent_resolve(conn),
                conversation_id=conversation_start.read_agent_conversation(
                    conn, CHIEF_SETTINGS_KEY
                ),
                sender_label="Panels",
                mode=PromptDeliveryMode.queue,
                reply_requested=False,
            )
    if isinstance(delivered.fate, PromptDeliverySuppressed):
        _LOG.info(
            "review transfer notice suppressed (ticket=%s reason=%s)",
            ticket_id,
            delivered.fate.reason,
        )
        return
    if isinstance(delivered.fate, (PromptDeliveryRefused, PromptDeliveryUncertain)):
        _LOG.warning(
            "review transfer notice did not reach its holder (ticket=%s, fate=%s)",
            ticket_id,
            delivered.fate,
        )
