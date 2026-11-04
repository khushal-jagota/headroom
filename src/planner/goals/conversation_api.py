"""Thin goal adapters for the shared conversation composer."""

from functools import partial

from fastapi import APIRouter

from planner.conversation.api import OwnerSendBody, conversation_message_content, delivery_fate_json
from planner.conversation.contracts import require_conversation_backend_key
from planner.core import authority
from planner.core.contracts import JsonDict, Principal, PrincipalKind
from planner.goals.conversation import goal_agent_key, resolve_goal
from planner.goals.data import read_goal
from planner.message_delivery.service import send_message
from planner.runtime import conversation_start
from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides
from planner.tickets.api import Clk, Conversations, Ctx, DbConn, MessageFiles

router = APIRouter()


@router.get("/goals/{goal_id}/conversation/start-values")
async def start_values(goal_id: str, conn: DbConn, ctx: Ctx) -> JsonDict:
    authority.require_above_or_self(conn, ctx.principal, authority.goal(goal_id))
    values = resolve_goal(conn, goal_id)
    return {
        "backend_key": values.backend_key.value,
        "model": values.model,
        "reasoning_effort": values.reasoning_effort,
    }


@router.post("/goals/{goal_id}/conversation/send")
async def send(
    goal_id: str,
    body: OwnerSendBody,
    conn: DbConn,
    ctx: Ctx,
    conversations: Conversations,
    message_files: MessageFiles,
    clk: Clk,
) -> JsonDict:
    authority.require_above(conn, ctx.principal, authority.goal(goal_id))
    read_goal(conn, goal_id)
    delivered = await send_message(
        conversations,
        conn,
        clk,
        ctx,
        Principal(PrincipalKind.goal, goal_id),
        partial(
            conversation_message_content,
            message_files,
            sent=body.content,
            sender_message_id=body.sender_message_id,
        ),
        conversation_id=body.conversation_id,
        runs_under=ConversationStartOverrides(
            backend_key=None
            if body.backend_key is None
            else require_conversation_backend_key(body.backend_key),
            model=body.model,
            reasoning_effort=body.reasoning_effort,
        ),
        mode=body.mode,
        owner_read_through_sequence=body.owner_read_through_sequence,
        sender_message_id=body.sender_message_id,
        sent_at_unix_milliseconds=body.sent_at_unix_milliseconds,
    )
    return {"conversation_id": delivered.conversation_id, **delivery_fate_json(delivered.fate)}


@router.post("/goals/{goal_id}/conversation/reset")
async def reset(goal_id: str, conn: DbConn, ctx: Ctx, conversations: Conversations) -> JsonDict:
    authority.require_above(conn, ctx.principal, authority.goal(goal_id))
    read_goal(conn, goal_id)
    await conversation_start.reset_agent_conversation(conversations, conn, goal_agent_key(goal_id))
    return {"conversation_id": None}
