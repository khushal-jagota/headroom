"""Feedback inbox HTTP routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from planner.core.authctx import require_direct_write, require_feedback_use
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.feedback import actions, views
from planner.tickets.api import (
    Clk,
    ConversationRecord,
    Conversations,
    Ctx,
    DbConn,
    body_opt_str,
    body_str,
    body_str_list,
)
from planner.work_attention import add_work_attention

router = APIRouter()


def _reject_unknown(raw: JsonDict, allowed: frozenset[str]) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise PlannerError(ErrorCode.validation, "unknown feedback fields", {"fields": unknown})


@router.get("/feedback")
async def list_feedback(
    conn: DbConn,
    conversations: Conversations,
    conversation_record: ConversationRecord,
) -> JsonDict:
    response = views.feedback_inbox_json(conn)
    tickets = [
        group["ticket"] for group in response["handled_groups"] if group["ticket"] is not None
    ]
    await add_work_attention(conn, conversations, conversation_record, tickets=tickets)
    return response


@router.get("/feedback/count")
async def feedback_count(conn: DbConn) -> JsonDict:
    return views.feedback_count_json(conn)


@router.post("/feedback")
async def create_feedback(raw: dict[str, Any], conn: DbConn, clk: Clk, ctx: Ctx) -> JsonDict:
    require_direct_write(ctx)
    _reject_unknown(raw, frozenset({"text", "page_address", "page_label"}))
    note = actions.create_feedback(
        conn,
        text=body_str(raw, "text"),
        page_address=body_opt_str(raw, "page_address"),
        page_label=body_opt_str(raw, "page_label"),
        now=clk.now_unix(),
    )
    return views.note_json(note)


@router.post("/feedback/{feedback_id}/dismiss")
async def dismiss_feedback(feedback_id: str, conn: DbConn, clk: Clk, ctx: Ctx) -> JsonDict:
    require_direct_write(ctx)
    return views.note_json(actions.dismiss_feedback(conn, feedback_id, now=clk.now_unix()))


@router.post("/feedback/{feedback_id}/reopen")
async def reopen_feedback(feedback_id: str, conn: DbConn, clk: Clk, ctx: Ctx) -> JsonDict:
    require_direct_write(ctx)
    return views.note_json(actions.reopen_feedback(conn, feedback_id, now=clk.now_unix()))


@router.post("/feedback/use")
async def use_feedback(raw: dict[str, Any], conn: DbConn, clk: Clk, ctx: Ctx) -> JsonDict:
    _reject_unknown(raw, frozenset({"feedback_ids", "ticket_id"}))
    feedback_ids = body_str_list(raw, "feedback_ids")
    ticket_id = body_str(raw, "ticket_id")
    notes = actions.use_feedback(
        conn,
        feedback_ids,
        ticket_id=ticket_id,
        now=clk.now_unix(),
        admit=lambda: require_feedback_use(conn, ctx, ticket_id),
    )
    return {"notes": [views.note_json(note) for note in notes]}
