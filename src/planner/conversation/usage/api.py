"""Stored summaries and the one explicit native-local refresh operation."""

from __future__ import annotations

import asyncio
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request

from planner.conversation.usage.contracts import UsageSourceSummary, UsageSummary
from planner.conversation.usage.service import RecordedUsageService

router = APIRouter(prefix="/usage", tags=["recorded usage"])


def _service(request: Request) -> RecordedUsageService:
    return cast(RecordedUsageService, request.app.state.recorded_usage)


@router.get("/summary")
def read_usage_summary(
    request: Request, days: int = 7, timezone: str = Query(default="UTC", max_length=128)
) -> UsageSummary:
    if days not in (1, 7, 30, 90):
        raise HTTPException(status_code=422, detail="days must be 1, 7, 30, or 90")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="timezone must name an IANA timezone") from None
    return _service(request).summary(days=days, timezone=timezone)


@router.post("/refresh")
async def refresh_usage(request: Request) -> dict[str, list[UsageSourceSummary]]:
    body = await request.body()
    if request.query_params or (body.strip() not in (b"", b"{}")):
        raise HTTPException(
            status_code=422, detail="native usage refresh accepts no source options"
        )
    sources = await asyncio.to_thread(_service(request).refresh)
    return {"sources": sources}
