"""Thin HTTP transport for the translated T3 summary and persisted provider allowances."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request

from planner.conversation.api import Runtime
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.usage.contracts.usage import UsageReadError
from planner.conversation.usage.providerLimits import presentProviderLimits

router = APIRouter(prefix="/usage", tags=["usage"])


def _input(
    sinceDay: str,
    untilDay: str,
    timeZone: str,
    resolution: str,
    sinceTime: str | None,
    untilTime: str | None,
) -> dict[str, Any]:
    try:
        ZoneInfo(timeZone)
        datetime.strptime(sinceDay, "%Y-%m-%d")
        datetime.strptime(untilDay, "%Y-%m-%d")
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(status_code=422, detail="Invalid usage date or time zone") from None
    value: dict[str, Any] = {
        "sinceDay": sinceDay,
        "untilDay": untilDay,
        "timeZone": timeZone,
        "resolution": resolution,
    }
    if sinceTime is not None:
        value["sinceTime"] = sinceTime
    if untilTime is not None:
        value["untilTime"] = untilTime
    return value


@router.get("/summary")
async def read_usage_summary(
    request: Request,
    sinceDay: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    untilDay: str = Query(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    timeZone: str = Query(default="UTC", min_length=1, max_length=128),
    resolution: str = Query(default="day", pattern=r"^(day|hour)$"),
    sinceTime: str | None = None,
    untilTime: str | None = None,
) -> dict[str, Any]:
    value = _input(sinceDay, untilDay, timeZone, resolution, sinceTime, untilTime)
    try:
        result = await request.app.state.recorded_usage.readSummary(value)
    except (UsageReadError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return cast(dict[str, Any], result)


@router.get("/limits")
async def read_usage_limits(runtime: Runtime) -> dict[str, Any]:
    if runtime.backend_state is None:
        return {}
    result: dict[str, Any] = {}
    for backend in ConversationBackendKey:
        observation = runtime.backend_state.read_usage(backend)
        if observation is not None:
            result[str(backend)] = presentProviderLimits(observation)
    return result


@router.post("/rates/refresh")
async def refresh_usage_rates(request: Request) -> dict[str, Any]:
    """T3's pricing-only refresh. This never invokes a provider or native peer."""
    return cast(dict[str, Any], await request.app.state.recorded_usage.refreshRates())
