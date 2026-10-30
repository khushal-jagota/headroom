"""Transport checks for T3's complete summary scan and the local allowance path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.usage.api import router
from planner.conversation.usage.localUsageLimits import LocalUsageLimits
from planner.conversation.usage.UsageService import UsageService

PARAMS = {
    "sinceDay": "2026-10-28",
    "untilDay": "2026-10-29",
    "timeZone": "Europe/Berlin",
    "resolution": "hour",
    "sinceTime": "2026-10-28T06:00:00.000Z",
    "untilTime": "2026-10-29T06:00:00.000Z",
}


@pytest.fixture
def usage_app(tmp_path: Path) -> FastAPI:
    stateDir = tmp_path / "state"
    stateDir.mkdir()
    projects = tmp_path / "claude"
    projects.mkdir()
    line = {
        "type": "assistant",
        "timestamp": "2026-10-28T12:00:00.000Z",
        "sessionId": "session",
        "requestId": "request",
        "message": {
            "id": "message",
            "model": "test-model",
            "usage": {"input_tokens": 100, "output_tokens": 5},
        },
    }
    (projects / "session.jsonl").write_text(json.dumps(line) + "\n")
    app = FastAPI()
    app.state.recorded_usage = UsageService(
        stateDir,
        [("claude", projects)],
        ratesFetcher=lambda: {
            "test-model": {"input_cost_per_token": 0.01, "output_cost_per_token": 0.02}
        },
        localUsageLimits=LocalUsageLimits(stateDir),
    )
    app.include_router(router, prefix="/api/conversation")
    return app


def test_summary_completes_first_read_without_native_refresh(usage_app: FastAPI) -> None:
    with TestClient(usage_app) as client:
        response = client.get("/api/conversation/usage/summary", params=PARAMS)
        assert response.status_code == 200
        summary = response.json()
        assert summary["contractVersion"] == 5
        assert summary["sources"][0]["status"] == "ok"
        assert summary["buckets"][0]["totals"]["uncachedInputTokens"] == 100
        assert summary["buckets"][0]["costUsd"] == 1.1
        assert client.post("/api/conversation/usage/refresh").status_code == 404


@pytest.mark.parametrize(
    "changed",
    [
        {"timeZone": "Invalid/Zone"},
        {"sinceDay": "2026-02-30"},
        {"resolution": "minute"},
        {"sinceTime": "2026-10-27T00:00:00.000Z"},
    ],
)
def test_invalid_window_is_rejected(usage_app: FastAPI, changed: dict[str, str]) -> None:
    response = TestClient(usage_app).get(
        "/api/conversation/usage/summary", params={**PARAMS, **changed}
    )
    assert response.status_code == 422


def test_initial_limits_uses_only_the_local_scanner(usage_app: FastAPI) -> None:
    class LocalOnlyService:
        def __init__(self) -> None:
            self.localUsageLimits = self
            self.inputs: list[dict[str, Any]] = []

        async def readSummary(self, value: dict[str, Any]) -> dict[str, Any]:
            self.inputs.append(value)
            return {}

        def current(self) -> dict[str, Any]:
            return {"codex": {"checkedAt": "2026-10-29T06:00:00Z", "windows": []}}

    service = LocalOnlyService()
    usage_app.state.recorded_usage = service
    result = TestClient(usage_app).get("/api/conversation/usage/limits")
    assert result.status_code == 200
    assert result.json() == service.current()
    assert len(service.inputs) == 1
    assert service.inputs[0]["resolution"] == "hour"
