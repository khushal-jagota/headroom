"""Resolve a Panels destination and use its canonical conversation send door."""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import (
    ConversationMessageContent,
    ConversationSystem,
    PromptDeliveryMode,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation.message_content import text_message_content
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageRecordedToOwner,
)
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import (
    NO_CONVERSATION_START_OVERRIDES,
    ConversationStartOverrides,
)
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.tickets import data as tickets_data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


async def send_ticket_system_message(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ticket_id: str,
    message: str,
    *,
    required_sprint_item_id: str | None = None,
) -> conversation_start.DeliveredMessage:
    """Send one Panels-authored lifecycle fact under the Ticket conversation lock."""
    async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
        ticket = tickets_data.read_ticket(conn, ticket_id)
        return await conversation_start.send_to_ticket_conversation(
            conversations,
            conn,
            ticket_id,
            text_message_content(message),
            conversation_id=ticket.conversation_id,
            sender_label="Panels",
            sender=None,
            recipient=None,
            now=clock.now_unix(),
            required_sprint_item_id=required_sprint_item_id,
        )


def sender_label(ctx: RequestContext) -> str:
    """Name the ordinary Panels caller without treating the label as authority."""
    if ctx.principal.kind is PrincipalKind.owner:
        return "owner"
    if ctx.principal.kind is PrincipalKind.chief:
        return "Chief"
    if ctx.principal.kind is PrincipalKind.ticket:
        return f"Ticket {ctx.principal.id}"
    return f"Sprint Item {ctx.principal.id}"


def _prompt_mode(mode: MessageDeliveryMode | PromptDeliveryMode) -> PromptDeliveryMode:
    if isinstance(mode, PromptDeliveryMode):
        return mode
    return {
        MessageDeliveryMode.queue: PromptDeliveryMode.queue,
        MessageDeliveryMode.steer: PromptDeliveryMode.steer,
        MessageDeliveryMode.send_now: PromptDeliveryMode.send_now,
    }[mode]


def _sender_conversation_id(conn: sqlite3.Connection, sender: Principal) -> str | None:
    if sender.kind is PrincipalKind.ticket:
        return tickets_data.read_ticket(conn, sender.id).conversation_id
    if sender.kind is PrincipalKind.chief:
        return conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
    if sender.kind is PrincipalKind.sprint_item:
        item = sprints_data.read_item(conn, sender.id).item
        return conversation_start.read_agent_conversation(conn, item.supervisor_agent_key)
    return None


def _sender_conversation_link_key(conn: sqlite3.Connection, sender: Principal) -> str:
    if sender.kind is PrincipalKind.ticket:
        return f"ticket:{sender.id}"
    if sender.kind is PrincipalKind.chief:
        return f"agent:{CHIEF_SETTINGS_KEY}"
    if sender.kind is PrincipalKind.sprint_item:
        item = sprints_data.read_item(conn, sender.id).item
        return f"agent:{item.supervisor_agent_key}"
    raise ValueError("the owner has no sender conversation link")


async def send_message(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ctx: RequestContext,
    recipient: Principal,
    message: str | ConversationMessageContent,
    mode: MessageDeliveryMode | PromptDeliveryMode = MessageDeliveryMode.queue,
    *,
    conversation_id: str | None = None,
    created_conversation_id: str | None = None,
    runs_under: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    sender_message_id: str | None = None,
    sent_at_unix_milliseconds: int | None = None,
    required_sprint_item_id: str | None = None,
) -> MessageDeliveryResult:
    """Send one text message to one resolved Panels conversation owner."""
    label = sender_label(ctx)
    content: ConversationMessageContent = (
        text_message_content(message) if isinstance(message, str) else message
    )
    prompt_mode = _prompt_mode(mode)
    sender = ctx.principal
    source_turn = None
    if sender.kind is not PrincipalKind.owner:
        source_conversation_id = _sender_conversation_id(conn, sender)
        if source_conversation_id is not None:
            source_turn = await conversations.active_turn_reference(source_conversation_id)

    if recipient.kind is PrincipalKind.owner:
        if sender.kind is PrincipalKind.owner:
            raise PlannerError(
                ErrorCode.validation,
                "the owner cannot send a message to the owner",
                {},
            )
        async with conversation_start.conversation_link_lock(
            _sender_conversation_link_key(conn, sender)
        ):
            sending_into = _sender_conversation_id(conn, sender)
            if sending_into is None:
                raise PlannerError(
                    ErrorCode.not_found,
                    "the sender has no current conversation",
                    {"kind": sender.kind.value, "id": sender.id},
                )
            resolved_content = await content(sending_into) if callable(content) else content
            await conversations.record_message_to_owner(
                sending_into,
                resolved_content,
                sender_label=label,
                sender=sender,
                recipient=recipient,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            )
        return MessageDeliveryResult(recipient, sending_into, MessageRecordedToOwner())

    if recipient.kind is PrincipalKind.ticket:
        ticket_id = recipient.id
        async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
            ticket = tickets_data.read_ticket(conn, ticket_id)
            delivered = await conversation_start.send_to_ticket_conversation(
                conversations,
                conn,
                ticket_id,
                content,
                conversation_id=(
                    conversation_id
                    if conversation_id is not None and sender.kind is PrincipalKind.owner
                    else ticket.conversation_id
                ),
                created_conversation_id=created_conversation_id
                or conversation_start.new_conversation_id(),
                runs_under=runs_under,
                sender_label=label,
                mode=prompt_mode,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                now=clock.now_unix(),
                required_sprint_item_id=required_sprint_item_id,
            )
    elif recipient.kind is PrincipalKind.chief:
        async with conversation_start.conversation_link_lock(f"agent:{CHIEF_SETTINGS_KEY}"):
            delivered = await conversation_start.send_to_agent_conversation(
                conversations,
                conn,
                CHIEF_SETTINGS_KEY,
                content,
                (
                    conversation_start.agent_resolve(conn)
                    if runs_under is NO_CONVERSATION_START_OVERRIDES
                    else conversation_start.agent_resolve(conn, runs_under)
                ),
                conversation_id=(
                    conversation_id
                    if conversation_id is not None and sender.kind is PrincipalKind.owner
                    else conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
                ),
                created_conversation_id=created_conversation_id
                or conversation_start.new_conversation_id(),
                runs_under=runs_under,
                sender_label=label,
                mode=prompt_mode,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
            )
    elif recipient.kind is PrincipalKind.sprint_item:
        item_id = recipient.id
        async with sprints_service.supervisor_lifecycle_lock(item_id):
            item = sprints_data.read_item(conn, item_id).item
            async with conversation_start.conversation_link_lock(
                f"agent:{item.supervisor_agent_key}"
            ):
                current = conversation_start.read_agent_conversation(
                    conn, item.supervisor_agent_key
                )
                resolved = (
                    conversation_start.sprint_item_supervisor_resolve(item)
                    if runs_under is NO_CONVERSATION_START_OVERRIDES
                    else conversation_start.sprint_item_supervisor_resolve(item, runs_under)
                )
                delivered = await conversation_start.send_to_agent_conversation(
                    conversations,
                    conn,
                    item.supervisor_agent_key,
                    content,
                    resolved,
                    conversation_id=(
                        conversation_id
                        if conversation_id is not None and sender.kind is PrincipalKind.owner
                        else current
                    ),
                    created_conversation_id=created_conversation_id
                    or conversation_start.new_conversation_id(),
                    runs_under=runs_under,
                    sender_label=label,
                    mode=prompt_mode,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    required_sprint_item_id=item_id,
                )
                if sender.kind is PrincipalKind.owner and (
                    (current is None and delivered.conversation_id is not None)
                    or (
                        isinstance(delivered.fate, PromptDeliveryStarted)
                        and (
                            runs_under.backend_key is not None
                            or runs_under.model is not None
                            or runs_under.reasoning_effort is not None
                        )
                    )
                ):
                    sprints_data.update_supervisor_launch_configuration(
                        conn,
                        item_id,
                        item.supervisor_launch_configuration.__class__(
                            employee_backend=resolved.backend_key,
                            employee_launch_model=resolved.model,
                            employee_launch_reasoning_effort=resolved.reasoning_effort,
                        ),
                        principal=sender,
                        clock=clock,
                    )
    else:
        raise PlannerError(
            ErrorCode.validation,
            "the owner does not have a deliverable conversation",
            {"kind": recipient.kind.value, "id": recipient.id},
        )

    result = MessageDeliveryResult(
        recipient=recipient,
        conversation_id=delivered.conversation_id,
        fate=delivered.fate,
    )
    if (
        source_turn is not None
        and not isinstance(delivered.fate, PromptDeliveryRefused)
        and delivered.newly_accepted
    ):
        await conversations.record_explicit_reply(source_turn, recipient)
    return result
