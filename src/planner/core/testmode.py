"""§9/§13 test-mode control router. Mounted under /api only when config.test_mode,
so /api/test/* is a plain 404 otherwise (the router is never built). set-now drives
the TestClock.

FastAPI appears here because this is part of the server shell (the server.py family)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from planner.core.clock import Clock, TestClock, parse_fake_now
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date, resolve_day_id
from planner.worker_types.configuration import configured_worker_type_registry


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

    @router.post("/test/run-step/{ticket_id}")
    async def run_step(ticket_id: str, request: Request) -> JsonDict:
        """Run ONE worker step for `ticket_id` against the composed conversation system.

        The same per-Ticket flow the readiness loop runs, driven by hand so a browser test
        does not have to wait for a poll. Test-gated: the router is mounted only when
        config.test_mode, so /api/test/* is a plain 404 otherwise."""
        # Imported at call time: the server module builds this router, and the flow
        # reaches back into the server module for the workspace folder.
        from planner.runtime.worker_step_readiness_loop import start_ready_worker_step

        started = await start_ready_worker_step(
            ticket_id,
            connect_database=request.app.state.conn_factory,
            conversation_system=request.app.state.conversation_system,
            worker_context_service=request.app.state.worker_context_service,
            worker_type_registry=configured_worker_type_registry(),
            planning_day_id_resolver=lambda: resolve_day_id(
                "today", clock.now(), config.boundary_hour
            ),
            now=clock.now_unix,
        )
        return {"dispatched": started, "ticket_id": ticket_id}

    return router
