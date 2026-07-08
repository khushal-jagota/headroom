"""§9/§13 test-mode control router. Mounted under /api only when config.test_mode,
so /api/test/* is a plain 404 otherwise (the router is never built). set-now drives
the TestClock.

FastAPI appears here because this is part of the server shell (the server.py family)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from planner.core.clock import Clock, TestClock, parse_fake_now
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date


def build_test_router(config: Config, clock: Clock) -> APIRouter:
    router = APIRouter()

    @router.post("/test/set-now")
    async def set_now(body: dict[str, Any]) -> JsonDict:
        raw = body.get("now")
        if not isinstance(raw, str) or not raw.strip():
            raise PlannerError(ErrorCode.validation, "body must carry an ISO 'now' string")
        try:
            parsed = parse_fake_now(raw)
        except ValueError as exc:
            raise PlannerError(ErrorCode.validation, f"invalid ISO datetime: {raw!r}") from exc
        if not isinstance(clock, TestClock):
            raise PlannerError(
                ErrorCode.validation, "clock is not a TestClock; start with PLAN_FAKE_NOW set"
            )
        clock.set(parsed)
        return {
            "now": clock.now().isoformat(),
            "planning_date": planning_date(clock.now(), config.boundary_hour).isoformat(),
        }

    return router
