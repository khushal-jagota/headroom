"""Direct-user HTTP write path for a finished Ticket's optional verdict."""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends

from planner.core.authctx import RequestContext, request_context, require_direct_write
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.judgments import data
from planner.judgments.contracts import VerdictBody
from planner.tickets.api import db_conn

router = APIRouter()

DbConn = Annotated[sqlite3.Connection, Depends(db_conn)]
Ctx = Annotated[RequestContext, Depends(request_context)]


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
        raise PlannerError(
            ErrorCode.validation, "invalid verdict text", {"text": text}
        )
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
            {"rating": verdict.rating, "text": verdict.text}
            if verdict is not None
            else None
        )
    }
