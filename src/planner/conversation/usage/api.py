"""Thin HTTP transport for the translated T3 summary and local allowance source."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request

from planner.conversation.usage.contracts.usage import UsageReadError

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
async def read_usage_limits(request: Request) -> dict[str, Any]:
    # A cold Limits page also observes local history. This uses the same scanner,
    # not a backend capability probe or the retired native refresh operation.
    until = datetime.now(UTC).replace(second=0, microsecond=0)
    since = until - timedelta(hours=24)
    value = {
        "sinceDay": since.date().isoformat(),
        "untilDay": until.date().isoformat(),
        "timeZone": "UTC",
        "resolution": "hour",
        "sinceTime": since.isoformat().replace("+00:00", "Z"),
        "untilTime": until.isoformat().replace("+00:00", "Z"),
    }
    service = request.app.state.recorded_usage
    await service.readSummary(value)
    return cast(dict[str, Any], service.localUsageLimits.current())


@router.post("/rates/refresh")
async def refresh_usage_rates(request: Request) -> dict[str, Any]:
    """T3's pricing-only refresh. This never invokes a provider or native peer."""
    return cast(dict[str, Any], await request.app.state.recorded_usage.refreshRates())
