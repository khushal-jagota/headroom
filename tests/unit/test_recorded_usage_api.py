"""Transport checks for T3's complete summary scan and the persisted provider allowance path."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.api import _runtime
from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageWindow,
    BackendUsageWindowKind,
)
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.usage.api import router
from planner.conversation.usage.UsageService import UsageService
from planner.core.db import connect, create_schema

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


def test_limits_reads_only_persisted_provider_observations(
    usage_app: FastAPI, tmp_path: Path
) -> None:
    db_path = str(tmp_path / "allowances.db")
    conn = connect(db_path)
    create_schema(conn)
    conn.close()
    state = BackendStateStore(db_path)
    observed = datetime(2026, 10, 27, 6, tzinfo=UTC)
    reset = datetime(2026, 10, 28, 6, tzinfo=UTC)
    state.keep_successful_usage(
        BackendUsageResult(
            backend_key=ConversationBackendKey.claude,
            outcome=BackendUsageOutcome.succeeded,
            observed_at=observed,
            windows=(
                BackendUsageWindow(BackendUsageWindowKind.five_hour, 12.5, reset),
                BackendUsageWindow(BackendUsageWindowKind.seven_day, 80, reset, "model-a"),
                BackendUsageWindow(BackendUsageWindowKind.seven_day, 20, reset, "model-b"),
            ),
        )
    )

    class Forbidden:
        def __getattr__(self, name: str) -> Any:
            raise AssertionError(f"passive Limits reached {name}")

    runtime = SimpleNamespace(backend_state=state)
    usage_app.dependency_overrides[_runtime] = lambda: runtime
    usage_app.state.recorded_usage = Forbidden()
    # Only backend_state exists. Catalogue, identity and provider operations cannot run.
    response = TestClient(usage_app).get("/api/conversation/usage/limits")
    assert response.status_code == 200
    assert response.json() == {
        "claude": {
            "checkedAt": "2026-10-27T06:00:00Z",
            "windows": [
                {
                    "id": "five_hour:",
                    "kind": "session",
                    "label": "5 hours",
                    "usedPercent": 12.5,
                    "resetsAt": "2026-10-28T06:00:00Z",
                    "windowDurationMins": 300,
                },
                {
                    "id": "seven_day:model-a",
                    "kind": "weekly",
                    "label": "7 days · model-a",
                    "usedPercent": 80,
                    "resetsAt": "2026-10-28T06:00:00Z",
                    "windowDurationMins": 10080,
                },
                {
                    "id": "seven_day:model-b",
                    "kind": "weekly",
                    "label": "7 days · model-b",
                    "usedPercent": 20,
                    "resetsAt": "2026-10-28T06:00:00Z",
                    "windowDurationMins": 10080,
                },
            ],
        }
    }
    # Missing observations invent neither a timestamp nor an allowance.
    empty = BackendStateStore(db_path)
    runtime.backend_state = empty
    assert "codex" not in TestClient(usage_app).get("/api/conversation/usage/limits").json()
    runtime.backend_state = None
    assert TestClient(usage_app).get("/api/conversation/usage/limits").json() == {}
