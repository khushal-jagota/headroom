"""The test-only Worker-step route exposes the real start result."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.testmode import build_test_router
from planner.runtime import worker_step_readiness_loop
from planner.runtime.worker_step_readiness_loop import WorkerStepStartResult


def test_run_step_reports_uncertain_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": "/tmp/panels-testmode-worker-step.db",
            "PLAN_FAKE_NOW": "2026-09-22T12:00:00+02:00",
        },
    )
    clock = build_clock(config)

    async def uncertain_start(*_args: Any, **_kwargs: Any) -> WorkerStepStartResult:
        return WorkerStepStartResult(started=False, delivery_fate="uncertain")

    monkeypatch.setattr(
        worker_step_readiness_loop,
        "start_ready_worker_step",
        uncertain_start,
    )
    app = FastAPI()
    app.state.conn_factory = object()
    app.state.conversation_system = object()
    app.include_router(build_test_router(config, clock), prefix="/api")

    with TestClient(app) as client:
        response = client.post("/api/test/run-step/t_example")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "dispatched": False,
        "delivery_fate": "uncertain",
        "ticket_id": "t_example",
    }
