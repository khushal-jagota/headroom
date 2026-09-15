"""Resolve a Panels destination and use its canonical conversation send door."""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import ConversationSystem, PromptDeliveryMode
from planner.conversation.message_content import text_message_content
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery.contracts import MessageDeliveryMode, MessageDeliveryResult
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.tickets import data as tickets_data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


def sender_label(ctx: RequestContext) -> str:
    """Name the ordinary Panels caller without treating the label as authority."""
    if ctx.principal.kind is PrincipalKind.owner:
        return "You"
    if ctx.principal.kind is PrincipalKind.chief:
        return "Chief"
    if ctx.principal.kind is PrincipalKind.ticket:
        return f"Ticket {ctx.principal.id}"
    return f"Sprint Item {ctx.principal.id}"


async def send_message(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ctx: RequestContext,
    recipient: Principal,
    message: str,
    mode: MessageDeliveryMode = MessageDeliveryMode.queue,
) -> MessageDeliveryResult:
    """Send one text message to one resolved Panels conversation owner."""
    label = sender_label(ctx)
    content = text_message_content(message)
    prompt_mode = {
        MessageDeliveryMode.queue: PromptDeliveryMode.run_when_free,
        MessageDeliveryMode.steer: PromptDeliveryMode.steer,
    }[mode]

    if recipient.kind is PrincipalKind.ticket:
        ticket_id = recipient.id
        ticket = tickets_data.read_ticket(conn, ticket_id)
        delivered = await conversation_start.send_to_ticket_conversation(
            conversations,
            conn,
            ticket_id,
            content,
            conversation_id=ticket.conversation_id,
            created_conversation_id=conversation_start.new_conversation_id(),
            sender_label=label,
            mode=prompt_mode,
            now=clock.now_unix(),
        )
    elif recipient.kind is PrincipalKind.chief:
        delivered = await conversation_start.send_to_agent_conversation(
            conversations,
            conn,
            CHIEF_SETTINGS_KEY,
            content,
            conversation_start.agent_resolve(conn),
            conversation_id=conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY),
            created_conversation_id=conversation_start.new_conversation_id(),
            sender_label=label,
            mode=prompt_mode,
        )
    elif recipient.kind is PrincipalKind.sprint_item:
        item_id = recipient.id
        async with sprints_service.supervisor_lifecycle_lock(item_id):
            item = sprints_data.read_item(conn, item_id).item
            delivered = await conversation_start.send_to_agent_conversation(
                conversations,
                conn,
                item.supervisor_agent_key,
                content,
                conversation_start.sprint_item_supervisor_resolve(item),
                conversation_id=conversation_start.read_agent_conversation(
                    conn, item.supervisor_agent_key
                ),
                created_conversation_id=conversation_start.new_conversation_id(),
                sender_label=label,
                mode=prompt_mode,
                required_sprint_item_id=item_id,
            )
    else:
        raise PlannerError(
            ErrorCode.validation,
            "the owner does not have a deliverable conversation",
            {"kind": recipient.kind.value, "id": recipient.id},
        )

    return MessageDeliveryResult(
        recipient=recipient,
        conversation_id=delivered.conversation_id,
        fate=delivered.fate,
    )
