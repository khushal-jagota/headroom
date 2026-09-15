"""HTTP write paths for Ticket judgment signals."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from planner.core.authctx import RequestContext, request_context, require_direct_write
from planner.core.contracts import JsonDict, Principal, PrincipalKind, principal_legacy_actor
from planner.core.errors import ErrorCode, PlannerError
from planner.judgments import data
from planner.judgments.contracts import TroubleNoteBody, VerdictBody
from planner.tickets.api import Clk, body_str, db_conn

router = APIRouter()

DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]


def _require_current_worker(ctx: RequestContext, ticket_id: str) -> None:
    if ctx.principal == Principal(PrincipalKind.ticket, ticket_id):
        return
    raise PlannerError(
        ErrorCode.agent_forbidden,
        "trouble can be recorded only by this ticket's worker",
        {"actor": principal_legacy_actor(ctx.principal), "ticket_id": ticket_id},
    )


def _optional_rating(body: JsonDict) -> int | None:
    rating = body.get("rating")
    if rating is None:
        return None
    if isinstance(rating, bool) or not isinstance(rating, int):
        raise PlannerError(
            ErrorCode.validation,
            "verdict rating must be an integer from 1 to 5",
            {"rating": rating},
        )
    return rating


def _optional_text(body: JsonDict) -> str | None:
    text = body.get("text")
    if text is None:
        return None
    if not isinstance(text, str):
        raise PlannerError(ErrorCode.validation, "invalid verdict text", {"text": text})
    return text


@router.put("/tickets/{ticket_id}/verdict")
async def put_ticket_verdict(
    ticket_id: str,
    body: JsonDict,
    conn: DbConn,
    ctx: Ctx,
) -> JsonDict:
    require_direct_write(ctx)
    verdict_body = VerdictBody(
        rating=_optional_rating(body),
        text=_optional_text(body),
    )
    verdict = data.upsert_verdict(
        conn,
        ticket_id,
        rating=verdict_body["rating"],
        text=verdict_body["text"],
    )
    return {
        "verdict": (
            {"rating": verdict.rating, "text": verdict.text} if verdict is not None else None
        )
    }


@router.post("/tickets/{ticket_id}/trouble-notes")
async def post_ticket_trouble_note(
    ticket_id: str,
    raw: JsonDict,
    conn: DbConn,
    ctx: Ctx,
    clk: Clk,
) -> JsonDict:
    _require_current_worker(ctx, ticket_id)
    body = TroubleNoteBody(body=body_str(raw, "body"))
    note = data.append_trouble_note(
        conn,
        ticket_id,
        body=body["body"],
        created_at=clk.now_unix(),
    )
    return {
        "trouble_note": {
            "sequence": note.sequence,
            "body": note.body,
            "created_at": note.created_at,
        }
    }
