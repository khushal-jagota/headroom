"""Goal-owned conversations through the existing non-Ticket conversation service."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationMessageContent,
    ConversationSystem,
    PromptDeliveryMode,
    PromptDeliveryStarted,
)
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.goals.data import read_goal
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import (
    NO_CONVERSATION_START_OVERRIDES,
    ConversationStartConfiguration,
    ConversationStartOverrides,
    ConversationStartValues,
    resolve_goal_conversation_start,
)


def goal_agent_key(goal_id: str) -> str:
    return "goal_" + goal_id


def resolve_goal(
    conn: sqlite3.Connection,
    goal_id: str,
    overrides: ConversationStartOverrides = NO_CONVERSATION_START_OVERRIDES,
    *,
    workspace_folder: Path | None = None,
) -> ConversationStartValues:
    goal = read_goal(conn, goal_id)
    return resolve_goal_conversation_start(
        goal_id=goal.id,
        launch_configuration=ConversationStartConfiguration(
            backend_key=ConversationBackendKey(goal.employee_backend),
            model=goal.employee_launch_model,
            reasoning_effort=goal.employee_launch_reasoning_effort,
        ),
        overrides=overrides,
        workspace_folder=(
            conversation_start._default_workspace_folder(conn)
            if workspace_folder is None
            else workspace_folder
        ),
    )


async def deliver_goal_message(
    conversations: ConversationSystem,
    conn: sqlite3.Connection,
    clock: Clock,
    ctx: RequestContext,
    recipient: Principal,
    content: ConversationMessageContent,
    mode: PromptDeliveryMode,
    *,
    conversation_id: str | None,
    created_conversation_id: str | None,
    runs_under: ConversationStartOverrides,
    sender_label: str,
    sender: Principal | None,
    owner_read_through_sequence: int | None,
    sender_message_id: str | None,
    sent_at_unix_milliseconds: int | None,
    reply_requested: bool,
) -> conversation_start.DeliveredMessage:
    key = goal_agent_key(recipient.id)
    async with conversation_start.conversation_link_lock("agent:" + key):
        goal = read_goal(conn, recipient.id)
        current = conversation_start.read_agent_conversation(conn, key)
        if (
            current is not None
            and runs_under.backend_key is not None
            and (runs_under.backend_key.value != goal.employee_backend)
        ):
            raise PlannerError(
                ErrorCode.validation, "an existing goal conversation cannot change backend", {}
            )
        resolved = resolve_goal(conn, recipient.id, runs_under)
        delivered = await conversation_start.send_to_agent_conversation(
            conversations,
            conn,
            key,
            content,
            resolved,
            conversation_id=(
                conversation_id
                if conversation_id is not None and ctx.principal.kind is PrincipalKind.owner
                else current
            ),
            created_conversation_id=created_conversation_id
            or conversation_start.new_conversation_id(),
            runs_under=runs_under,
            sender_label=sender_label,
            mode=mode,
            owner_read_through_sequence=owner_read_through_sequence,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
            required_goal_id=goal.id,
        )
        if ctx.principal.kind is PrincipalKind.owner and (
            (current is None and delivered.conversation_id is not None)
            or (
                isinstance(delivered.fate, PromptDeliveryStarted)
                and runs_under != NO_CONVERSATION_START_OVERRIDES
            )
        ):
            # Launch choices belong to the goal; page edits have their separate revision.
            with conn:
                conn.execute(
                    "UPDATE goals SET employee_backend=?, employee_launch_model=?, "
                    "employee_launch_reasoning_effort=? WHERE id=? AND employee_backend=? "
                    "AND employee_launch_model=? AND employee_launch_reasoning_effort IS ?",
                    (
                        resolved.backend_key.value,
                        resolved.model,
                        resolved.reasoning_effort,
                        goal.id,
                        goal.employee_backend,
                        goal.employee_launch_model,
                        goal.employee_launch_reasoning_effort,
                    ),
                )
        return delivered
