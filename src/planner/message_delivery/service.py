"""Resolve a Panels destination and use its canonical conversation send door."""

from __future__ import annotations

import sqlite3

from planner.conversation.contracts import (
    ConversationMessageContent,
    ConversationSystem,
    NativeMessageOperation,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryStarted,
)
from planner.conversation.message_content import text_message_content
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.task_settlement import run_on_worker_thread
from planner.managers import data as managers_data
from planner.managers import service as managers_service
from planner.message_delivery.contracts import (
    MessageAdmission,
    MessageDeliveryMode,
    MessageDeliveryResult,
    MessageRecordedToOwner,
)
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import (
    NO_CONVERSATION_START_OVERRIDES,
    ConversationStartOverrides,
    overrides_for_existing_conversation,
)
from planner.tickets import data as tickets_data
from planner.worker_settings.service import CHIEF_SETTINGS_KEY


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
    system_message: bool = False,
    admission: MessageAdmission | None = None,
    recording_receipt: NativeMessageOperation | None = None,
) -> MessageDeliveryResult:
    """Send one text message to one resolved Panels conversation owner.

    ``system_message`` records an automatic action as Panels without changing the caller's
    authority or launch choices. Only trusted internal callers select it.
    """
    caller = ctx.principal
    message_kinds = {
        PrincipalKind.owner,
        PrincipalKind.chief,
        PrincipalKind.manager,
        PrincipalKind.ticket,
    }
    if caller.kind not in message_kinds or recipient.kind not in message_kinds:
        raise PlannerError(ErrorCode.validation, "unsupported message principal kind", {})
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
                recording_receipt=recording_receipt,
            )
        return MessageDeliveryResult(recipient, sending_into, MessageRecordedToOwner())

    try:
        if recipient.kind is PrincipalKind.ticket:
            ticket_id = recipient.id
            async with conversation_start.conversation_link_lock(f"ticket:{ticket_id}"):
                ticket = tickets_data.read_ticket(conn, ticket_id)
                if admission is not None:
                    prior = await admission.preflight(
                        tickets_data.read_ticket(conn, ticket_id).conversation_id
                    )
                    if prior is not None:
                        return prior
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
                    now=clock.now_unix(),
                    recording_receipt=recording_receipt,
                )
        elif recipient.kind is PrincipalKind.chief:
            async with conversation_start.conversation_link_lock(f"agent:{CHIEF_SETTINGS_KEY}"):
                if admission is not None:
                    prior = await admission.preflight(
                        conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
                    )
                    if prior is not None:
                        return prior
                current = conversation_start.read_agent_conversation(conn, CHIEF_SETTINGS_KEY)
                target = (
                    conversation_id
                    if conversation_id is not None and caller.kind is PrincipalKind.owner
                    else current
                )
                # Startup settings belong to a new conversation. A message into one that
                # exists, or into a named one that does not, needs them only for a model.
                choice = (
                    runs_under
                    if target is None
                    else overrides_for_existing_conversation(runs_under)
                )
                delivered = await conversation_start.send_to_agent_conversation(
                    conversations,
                    conn,
                    CHIEF_SETTINGS_KEY,
                    content,
                    (
                        None
                        if target is not None and choice.model is None
                        else conversation_start.agent_resolve(conn)
                        if choice == NO_CONVERSATION_START_OVERRIDES
                        else conversation_start.agent_resolve(conn, choice)
                    ),
                    conversation_id=target,
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
                    recording_receipt=recording_receipt,
                )
        elif recipient.kind is PrincipalKind.manager:
            item_id = recipient.id
            async with managers_service.manager_lifecycle_lock(item_id):
                item = managers_data.read_manager(conn, item_id).item
                async with conversation_start.conversation_link_lock(
                    f"agent:{item.manager_agent_key}"
                ):
                    current = conversation_start.read_agent_conversation(
                        conn, item.manager_agent_key
                    )
                    if admission is not None:
                        prior = await admission.preflight(current)
                        if prior is not None:
                            return prior
                    target = (
                        conversation_id
                        if conversation_id is not None and caller.kind is PrincipalKind.owner
                        else current
                    )
                    # A message into an existing conversation changes nothing by naming
                    # its backend, so only a start reads that backend as a choice.
                    choice = (
                        runs_under
                        if target is None
                        else overrides_for_existing_conversation(runs_under)
                    )
                    resolved = (
                        conversation_start.manager_resolve(conn, item)
                        if choice == NO_CONVERSATION_START_OVERRIDES
                        else conversation_start.manager_resolve(conn, item, choice)
                    )
                    explicit_choice = (
                        choice.backend_key is not None
                        or choice.model is not None
                        or choice.reasoning_effort is not None
                        or choice.reasoning_effort_is_set
                    )
                    if explicit_choice:
                        from planner.managers.launch_settings import validate_launch_configuration

                        validate_launch_configuration(
                            conn,
                            item.manager_launch_configuration.__class__(
                                resolved.backend_key, resolved.model, resolved.reasoning_effort
                            ),
                        )
                    delivered = await conversation_start.send_to_agent_conversation(
                        conversations,
                        conn,
                        item.manager_agent_key,
                        content,
                        resolved,
                        conversation_id=target,
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
                        required_manager_id=item_id,
                        retain_created_conversation=retain_created_conversation,
                        recording_receipt=recording_receipt,
                    )
                    if caller.kind is PrincipalKind.owner and (
                        (current is None and delivered.conversation_id is not None)
                        or (
                            isinstance(
                                delivered.fate, (PromptDeliveryStarted, PromptDeliveryQueued)
                            )
                            and explicit_choice
                        )
                    ):
                        # A write, so it waits on the SQLite lock on a worker thread.
                        await run_on_worker_thread(
                            managers_data.update_manager_launch_configuration,
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
                            explicit_choice=explicit_choice,
                        )
        else:
            raise PlannerError(
                ErrorCode.validation,
                "the owner does not have a deliverable conversation",
                {"kind": recipient.kind.value, "id": recipient.id},
            )
    except BaseException:
        if admission is not None:
            # Both admission steps write, so they wait on the SQLite lock on a worker thread.
            await run_on_worker_thread(admission.abort_before_dispatch)
        raise

    if admission is not None:
        await run_on_worker_thread(admission.finish, delivered.fate)

    return MessageDeliveryResult(
        recipient=recipient,
        conversation_id=delivered.conversation_id,
        fate=delivered.fate,
    )
