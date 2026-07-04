"""§9/§13 test-mode control router. Mounted under /api only when config.test_mode,
so /api/test/* is a plain 404 otherwise (the router is never built). set-now drives
the TestClock; the two tick endpoints call T11's runtime through a lazy import seam
and answer 501 until it lands.

FastAPI appears here because this is part of the server shell (the server.py family)."""

from __future__ import annotations

import importlib
import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from planner.core.adapters.registry import Adapters
from planner.core.clock import Clock, TestClock, parse_fake_now
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date


def _not_wired() -> JSONResponse:
    # Returned directly (never raised as a PlannerError, which the handler would map
    # to 400): the seam pins this exact body for T11's tests to compare whole.
    return JSONResponse(
        status_code=501,
        content={"error": {"code": "validation", "message": "runtime not wired yet"}},
    )


def _lazy(module_name: str, attr: str) -> Any | None:
    # importlib (not a from-import) keeps the not-yet-existing T11 modules out of the
    # static import graph; ImportError/AttributeError are the seam's named failures.
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr)
    except (ImportError, AttributeError):
        return None


def build_test_router(
    config: Config,
    clock: Clock,
    adapters: Adapters,
    conn_factory: Callable[[], sqlite3.Connection],
) -> APIRouter:
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

    @router.post("/test/tick-boundary")
    async def tick_boundary() -> JSONResponse:
        fn = _lazy("planner.days.scheduler", "run_boundary_tick")
        if fn is None:
            return _not_wired()
        report = fn(conn_factory, config, clock, adapters)
        return JSONResponse(status_code=200, content=report)

    @router.post("/test/tick-dispatcher")
    async def tick_dispatcher() -> JSONResponse:
        fn = _lazy("planner.dispatch.runtime", "run_tick")
        if fn is None:
            return _not_wired()
        report = fn(conn_factory, config, clock, adapters)
        return JSONResponse(status_code=200, content=report)

    return router
