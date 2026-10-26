"""The native read API never scans implicitly or accepts browser-controlled sources."""

from __future__ import annotations

import threading
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.unit.test_recorded_usage import NOW, native_line

from planner.conversation.usage.api import router
from planner.conversation.usage.contracts import UsageSource
from planner.conversation.usage.service import RecordedUsageService
from planner.core.db import connect


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




def test_refresh_rejects_source_paths_and_credentials(
    usage_app: tuple[FastAPI, Path, list[int]]
) -> None:
    app, _, scanned_on = usage_app
    client = TestClient(app)
    refused = client.post("/api/conversation/usage/refresh", json={"source": "/private"})
    assert refused.status_code == 422
    assert client.post("/api/conversation/usage/refresh?root=/private").status_code == 422
    assert scanned_on == []
