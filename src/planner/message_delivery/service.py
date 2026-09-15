"""Resolve a Panels destination and use its canonical conversation send door."""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import ConversationSystem, PromptDeliveryMode
from planner.conversation.message_content import text_message_content
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageTarget,
    MessageTargetType,
    ResolvedMessageDestination,
)
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.tickets import data as tickets_data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


def sender_label(ctx: RequestContext) -> str:
    """Name the ordinary Panels caller without treating the label as authority."""
    if not ctx.is_attributed:
        return "You"
    if ctx.actor == "chief":
        return "Chief"
    if ctx.actor == "worker" and ctx.ticket_id is not None:
        return f"Ticket {ctx.ticket_id}"
    if ctx.actor == "sprint_item_supervisor" and ctx.sprint_item_id is not None:
        return f"Sprint Item {ctx.sprint_item_id}"
    return ctx.actor


async def send_message(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ctx: RequestContext,
    target: MessageTarget,
    message: str,
    mode: MessageDeliveryMode = MessageDeliveryMode.queue,
) -> MessageDeliveryResult:
    """Send one text message to one resolved Panels conversation owner."""
    label = sender_label(ctx)
    content = text_message_content(message)
    prompt_mode = {
        MessageDeliveryMode.queue: PromptDeliveryMode.queue,
        MessageDeliveryMode.steer: PromptDeliveryMode.steer,
        MessageDeliveryMode.send_now: PromptDeliveryMode.send_now,
    }[mode]

    if target.target_type is MessageTargetType.ticket:
        ticket_id = _required_target_id(target)
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
        resolved = ResolvedMessageDestination("ticket", ticket_id)
    elif target.target_type is MessageTargetType.chief:
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
        resolved = ResolvedMessageDestination("agent", CHIEF_SETTINGS_KEY)
    elif target.target_type is MessageTargetType.sprint_item:
        item_id = _required_target_id(target)
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
        resolved = ResolvedMessageDestination("agent", item.supervisor_agent_key)
    else:
        agent_key = _required_target_id(target)
        row = conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key = ?", (agent_key,)
        ).fetchone()
        if row is None:
            raise PlannerError(
                ErrorCode.not_found,
                "agent not found",
                {"agent_key": agent_key},
            )
        conversation_id = None if row["conversation_id"] is None else str(row["conversation_id"])
        if conversation_id is None:
            raise PlannerError(
                ErrorCode.validation,
                "agent has no current conversation and no start configuration",
                {"agent_key": agent_key},
            )
        delivered = await conversation_start.send_to_agent_conversation(
            conversations,
            conn,
            agent_key,
            content,
            None,
            conversation_id=conversation_id,
            sender_label=label,
            mode=prompt_mode,
        )
        resolved = ResolvedMessageDestination("agent", agent_key)

    return MessageDeliveryResult(
        target=target,
        resolved_destination=resolved,
        conversation_id=delivered.conversation_id,
        fate=delivered.fate,
    )


def _required_target_id(target: MessageTarget) -> str:
    if target.target_id is None:  # guarded by the API contract
        raise AssertionError(f"{target.target_type} target has no id")
    return target.target_id
