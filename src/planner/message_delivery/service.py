"""Resolve a Panels destination and use its canonical conversation send door."""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import (
    ConversationMessageContent,
    ConversationSystem,
    ConversationTurnReference,
    PromptDeliveryMode,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliverySuppressed,
)
from planner.conversation.message_content import text_message_content
from planner.core import authority
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.managers import data as managers_data
from planner.managers import service as managers_service
from planner.message_delivery.contracts import (
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageRecordedToOwner,
)
from planner.message_delivery.logic.permissions import permits_manager_message
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import (
    NO_CONVERSATION_START_OVERRIDES,
    ConversationStartOverrides,
)
from planner.tickets import data as tickets_data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


async def revision_source_turn(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    *,
    ctx: RequestContext,
) -> ConversationTurnReference | None:
    """Capture the exact source turn that the committed rejection answers."""
    if ctx.principal.kind is PrincipalKind.owner:
        return None
    source_conversation_id = _sender_conversation_id(conn, ctx.principal)
    if source_conversation_id is None:
        return None
    return await conversations.active_turn_reference(source_conversation_id)


def sender_label(ctx: RequestContext) -> str:
    """Name the ordinary Panels caller without treating the label as authority."""
    if ctx.principal.kind is PrincipalKind.owner:
        return "owner"
    if ctx.principal.kind is PrincipalKind.chief:
        return "Chief"
    if ctx.principal.kind is PrincipalKind.ticket:
        return f"Ticket {ctx.principal.id}"
    return f"Manager {ctx.principal.id}"


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
    if sender.kind is PrincipalKind.manager:
        item = managers_data.read_manager(conn, sender.id).item
        return conversation_start.read_agent_conversation(conn, item.manager_agent_key)
    return None


def _sender_conversation_link_key(conn: sqlite3.Connection, sender: Principal) -> str:
    if sender.kind is PrincipalKind.ticket:
        return f"ticket:{sender.id}"
    if sender.kind is PrincipalKind.chief:
        return f"agent:{CHIEF_SETTINGS_KEY}"
    if sender.kind is PrincipalKind.manager:
        item = managers_data.read_manager(conn, sender.id).item
        return f"agent:{item.manager_agent_key}"
    raise ValueError("the owner has no sender conversation link")


def _as_target(principal: Principal) -> authority.Target | None:
    """The thing this principal is, when something can stand above it.

    Khushal and the Chief are above everything, so nothing is above them and they are no
    target. A message addressed to either asks no authority question.
    """
    if principal.kind is PrincipalKind.ticket:
        return authority.ticket(principal.id)
    if principal.kind is PrincipalKind.manager:
        return authority.manager(principal.id)
    return None


def require_reach(conn: sqlite3.Connection, caller: Principal, recipient: Principal) -> None:
    """Sending down the chain is acting on the recipient. Sending up is only speaking.

    Two principals in one chain can talk, in both directions. Standing above the
    recipient is acting on it — starting a turn in a conversation that belongs to
    something below you — and that is the rule's own question. Standing below it is not:
    a Worker answering the Outcome that asked it something is speaking, and refusing that
    would make the reply every addressed prompt requires impossible to send.

    Existing ordinary Managers can also speak to one another. This
    message-only permission grants no authority over either supervisor or its Tickets.
    Other strangers are neither, and they refuse.

    Asked by the two routes that take a recipient from their caller, and it lives here so
    they ask the same question rather than two. It is not asked inside ``send_message``:
    the other callers name a recipient the route has already guarded, and a Ticket asking
    its own ceiling holder for help names a principal Panels addressed it to, which is not
    the caller reaching anywhere.
    """
    if permits_manager_message(caller, recipient):
        managers_data.read_manager(conn, caller.id)
        managers_data.read_manager(conn, recipient.id)
        return
    target = _as_target(recipient)
    if target is None:
        return
    if authority.is_above(conn, caller, target):
        return
    caller_target = _as_target(caller)
    if caller_target is not None and authority.is_above(conn, recipient, caller_target):
        return
    authority.require_above(conn, caller, target)


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
    owner_read_through_sequence: int | None = None,
    sender_message_id: str | None = None,
    sent_at_unix_milliseconds: int | None = None,
    retain_created_conversation: bool = False,
    expects_reply: bool = True,
    system_message: bool = False,
) -> MessageDeliveryResult:
    """Send one text message to one resolved Panels conversation owner.

    ``system_message`` records an automatic action as Panels without changing the caller's
    authority or launch choices. Only trusted internal callers select it. A system action
    creates no reply requirement and cannot settle the caller's existing reply obligation.

    ``expects_reply`` is False for a message whose answer is a thing the recipient does
    rather than words it sends back. Such a message adds no reply requirement to the wire
    prompt and leaves no missing-reply marker when the turn ends.
    """
    caller = ctx.principal
    if owner_read_through_sequence is not None and (
        caller.kind is not PrincipalKind.owner or system_message
    ):
        raise ValueError("only the owner can acknowledge messages with a reply")
    if system_message and recipient.kind is PrincipalKind.owner:
        raise PlannerError(
            ErrorCode.validation,
            "a system message cannot be addressed to the owner",
            {},
        )
    label = "Panels" if system_message else sender_label(ctx)
    content: ConversationMessageContent = (
        text_message_content(message) if isinstance(message, str) else message
    )
    prompt_mode = _prompt_mode(mode)
    sender = None if system_message else caller
    source_turn = None
    if not system_message and caller.kind is not PrincipalKind.owner:
        source_conversation_id = _sender_conversation_id(conn, caller)
        if source_conversation_id is not None:
            source_turn = await conversations.active_turn_reference(source_conversation_id)
    is_reply = bool(
        source_turn is not None and await conversations.turn_expects_reply(source_turn, recipient)
    )

    if recipient.kind is PrincipalKind.owner:
        if caller.kind is PrincipalKind.owner:
            raise PlannerError(
                ErrorCode.validation,
                "the owner cannot send a message to the owner",
                {},
            )
        async with conversation_start.conversation_link_lock(
            _sender_conversation_link_key(conn, caller)
        ):
            sending_into = _sender_conversation_id(conn, caller)
            if sending_into is None:
                raise PlannerError(
                    ErrorCode.not_found,
                    "the sender has no current conversation",
                    {"kind": caller.kind.value, "id": caller.id},
                )
            resolved_content = await content(sending_into) if callable(content) else content
            await conversations.record_message_to_owner(
                sending_into,
                resolved_content,
                sender_label=label,
                sender=caller,
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
                    if conversation_id is not None and caller.kind is PrincipalKind.owner
                    else ticket.conversation_id
                ),
                created_conversation_id=created_conversation_id
                or conversation_start.new_conversation_id(),
                runs_under=runs_under,
                sender_label=label,
                mode=prompt_mode,
                owner_read_through_sequence=owner_read_through_sequence,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                reply_requested=not system_message and expects_reply and not is_reply,
                now=clock.now_unix(),
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
                    if conversation_id is not None and caller.kind is PrincipalKind.owner
                    else conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
                ),
                created_conversation_id=created_conversation_id
                or conversation_start.new_conversation_id(),
                runs_under=runs_under,
                sender_label=label,
                mode=prompt_mode,
                owner_read_through_sequence=owner_read_through_sequence,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                sender=sender,
                recipient=recipient,
                reply_requested=not system_message and expects_reply and not is_reply,
            )
    elif recipient.kind is PrincipalKind.manager:
        item_id = recipient.id
        async with managers_service.manager_lifecycle_lock(item_id):
            item = managers_data.read_manager(conn, item_id).item
            async with conversation_start.conversation_link_lock(f"agent:{item.manager_agent_key}"):
                current = conversation_start.read_agent_conversation(conn, item.manager_agent_key)
                resolved = (
                    conversation_start.manager_resolve(item)
                    if runs_under is NO_CONVERSATION_START_OVERRIDES
                    else conversation_start.manager_resolve(item, runs_under)
                )
                delivered = await conversation_start.send_to_agent_conversation(
                    conversations,
                    conn,
                    item.manager_agent_key,
                    content,
                    resolved,
                    conversation_id=(
                        conversation_id
                        if conversation_id is not None and caller.kind is PrincipalKind.owner
                        else current
                    ),
                    created_conversation_id=created_conversation_id
                    or conversation_start.new_conversation_id(),
                    runs_under=runs_under,
                    sender_label=label,
                    mode=prompt_mode,
                    owner_read_through_sequence=owner_read_through_sequence,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    sender=sender,
                    recipient=recipient,
                    reply_requested=not system_message and expects_reply and not is_reply,
                    required_manager_id=item_id,
                    retain_created_conversation=retain_created_conversation,
                )
                if caller.kind is PrincipalKind.owner and (
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
                    managers_data.update_manager_launch_configuration(
                        conn,
                        item_id,
                        item.manager_launch_configuration.__class__(
                            employee_backend=resolved.backend_key,
                            employee_launch_model=resolved.model,
                            employee_launch_reasoning_effort=resolved.reasoning_effort,
                        ),
                        principal=caller,
                        clock=clock,
                        expected_configuration=item.manager_launch_configuration,
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
        and not isinstance(delivered.fate, (PromptDeliveryRefused, PromptDeliverySuppressed))
        and delivered.newly_accepted
    ):
        await conversations.record_explicit_reply(source_turn, recipient)
    return result
