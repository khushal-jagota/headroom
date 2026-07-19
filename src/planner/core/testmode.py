"""§9/§13 test-mode control router. Mounted under /api only when config.test_mode,
so /api/test/* is a plain 404 otherwise (the router is never built). set-now drives
the TestClock.

FastAPI appears here because this is part of the server shell (the server.py family)."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Request

from planner.core.clock import Clock, TestClock, parse_fake_now
from planner.core.config import Config
from planner.core.contracts import JsonDict
from planner.core.errors import ErrorCode, PlannerError
from planner.days.logic.dates import planning_date


class _TestModeEmployeeRevisionHandoff:
    def __init__(
        self,
        decision: threading.Event,
        choose: list[str],
        choice_lock: threading.Lock,
    ) -> None:
        self._decision = decision
        self._choose = choose
        self._choice_lock = choice_lock

    def _finish(self, choice: str) -> None:
        with self._choice_lock:
            if not self._choose:
                self._choose.append(choice)
                self._decision.set()

    def release(self) -> None:
        self._finish("released")

    def cancel(self) -> None:
        self._finish("cancelled")


class TestModeAcceptingEmployeeRevisionRunner:
    """Hermetic handoff acceptance for browser tests; it performs no Hermes work."""

    __test__ = False

    def __init__(self) -> None:
        self.decisions: list[tuple[str, str, str]] = []
        self._decisions_lock = threading.Lock()

    def reserve_revision(
        self, ticket_id: str, guidance: str
    ) -> _TestModeEmployeeRevisionHandoff:
        parked = threading.Event()
        decision = threading.Event()
        choice: list[str] = []
        choice_lock = threading.Lock()

        def wait_for_decision() -> None:
            parked.set()
            decision.wait()
            with self._decisions_lock:
                self.decisions.append((ticket_id, guidance, choice[0]))

        threading.Thread(
            target=wait_for_decision,
            name=f"test-employee-revision-{ticket_id}",
            daemon=True,
        ).start()
        parked.wait()
        return _TestModeEmployeeRevisionHandoff(decision, choice, choice_lock)


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
        """Dispatch ONE real automatic Employee step for `ticket_id` through the composed
        runner (S3 Collision #B (a)). Relay test mode composes the REAL EmployeeStepRunner over
        the PoolStepGateway (server.py), so this drives a genuine dispatch against the scripted
        child — the discovery loop is not needed. Test-gated: the router is mounted only when
        config.test_mode, so /api/test/* is a plain 404 otherwise."""
        runner = getattr(request.app.state, "employee_step_runner", None)
        if runner is None or not hasattr(runner, "try_run_automatic_step"):
            raise PlannerError(
                ErrorCode.gateway_offline,
                "no composed employee step runner in this test composition",
                {"ticket_id": ticket_id},
            )
        runner.try_run_automatic_step(ticket_id)
        return {"dispatched": True, "ticket_id": ticket_id}

    return router
