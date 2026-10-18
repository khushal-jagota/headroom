"""The native read API never scans implicitly or accepts browser-controlled sources."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from sqlite3 import Connection

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.unit.test_recorded_usage import NOW, native_line

from planner.conversation.usage.api import router
from planner.conversation.usage.contracts import UsageSource
from planner.conversation.usage.service import RecordedUsageService
from planner.core.clock import TestClock as PanelsTestClock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app


@pytest.fixture
def usage_app(tmp_db: Connection, tmp_path: Path) -> tuple[FastAPI, Path, list[int]]:
    database = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    root = tmp_path / "codex"
    path = root / "sessions" / "sample.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(native_line("first"))
    scanned_on: list[int] = []

    def sources() -> tuple[UsageSource, ...]:
        scanned_on.append(threading.get_ident())
        return (UsageSource("codex", root),)

    app = FastAPI()
    app.state.recorded_usage = RecordedUsageService(
        lambda: connect(database), sources=sources, now=lambda: NOW
    )
    app.include_router(router, prefix="/api/conversation")
    return app, path, scanned_on


def test_summary_reads_stored_records_and_explicit_refresh_is_required(
    usage_app: tuple[FastAPI, Path, list[int]]
) -> None:
    app, path, scanned_on = usage_app
    with TestClient(app) as client:
        summary = client.get("/api/conversation/usage/summary?days=7&timezone=Europe/Berlin")
        assert summary.status_code == 200
        assert summary.json()["totals"]["total_tokens"] is None
        assert scanned_on == []
        response = client.post("/api/conversation/usage/refresh")
        assert response.status_code == 200
        assert response.json()["sources"][0]["state"] == "ready"
        path.write_text(native_line("new", count=300))
        stored = client.get("/api/conversation/usage/summary").json()
        assert stored["scope"] == "local_cli"
        assert stored["totals"]["total_tokens"] == 105
        assert len(scanned_on) == 1


@pytest.mark.parametrize("query", ["days=0", "days=2", "timezone=invalid/zone", "timezone=../x"])
def test_invalid_summary_filters_are_rejected(
    usage_app: tuple[FastAPI, Path, list[int]], query: str
) -> None:
    app, _, scanned_on = usage_app
    assert TestClient(app).get("/api/conversation/usage/summary?" + query).status_code == 422
    assert scanned_on == []


def test_refresh_rejects_source_paths_and_credentials(
    usage_app: tuple[FastAPI, Path, list[int]]
) -> None:
    app, _, scanned_on = usage_app
    client = TestClient(app)
    refused = client.post("/api/conversation/usage/refresh", json={"source": "/private"})
    assert refused.status_code == 422
    assert client.post("/api/conversation/usage/refresh?root=/private").status_code == 422
    assert scanned_on == []


def test_native_refresh_runs_outside_the_event_loop_thread(
    usage_app: tuple[FastAPI, Path, list[int]]
) -> None:
    app, _, scanned_on = usage_app

    async def exercise() -> None:
        event_loop_thread = threading.get_ident()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://usage.test"
        ) as client:
            response = await client.post("/api/conversation/usage/refresh")
            assert response.status_code == 200
        assert scanned_on and all(thread != event_loop_thread for thread in scanned_on)

    asyncio.run(exercise())


def test_application_wires_the_stored_usage_routes_without_a_native_read(
    tmp_db: Connection, tmp_path: Path
) -> None:
    database = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    config = load_config(path=None, env={
        "PLAN_TEST_MODE": "1", "PLAN_DB_PATH": database,
        "PLAN_LOGS_DIR": str(tmp_path / "logs"),
    })
    app = create_app(config, PanelsTestClock(NOW), lambda: connect(database))
    # No lifespan or backend process is required to read this stored-only resource.
    response = TestClient(app).get("/api/conversation/usage/summary")
    assert response.status_code == 200
    assert response.json()["scope"] == "local_cli"
    assert response.json()["sources"][0]["observed_at"] is None
