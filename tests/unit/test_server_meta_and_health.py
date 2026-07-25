"""What the server says about itself: /api/meta and the release-SHA readiness gate."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

RELEASE_SHA = "0123456789abcdef0123456789abcdef01234567"


def _make_app(tmp_path: Path, **extra_environment: str) -> FastAPI:
    db_path = tmp_path / "meta.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path), **extra_environment},
    )

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), conn_factory)


def test_meta_carries_only_the_two_facts_the_browser_needs(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/meta")

    assert response.status_code == 200
    assert response.json() == {"test_mode": True, "release_sha": None}


def test_meta_reports_the_configured_release_sha(tmp_path: Path) -> None:
    app = _make_app(tmp_path, PLAN_RELEASE_SHA=RELEASE_SHA)

    with TestClient(app) as client:
        response = client.get("/api/meta")

    assert response.json() == {"test_mode": True, "release_sha": RELEASE_SHA}


def test_health_is_ready_only_for_a_caller_that_names_the_running_release(
    tmp_path: Path,
) -> None:
    app = _make_app(tmp_path, PLAN_RELEASE_SHA=RELEASE_SHA)

    with TestClient(app) as client:
        matching = client.get("/api/health", params={"expected_sha": RELEASE_SHA})
        unnamed = client.get("/api/health")
        wrong = client.get("/api/health", params={"expected_sha": "f" * 40})

    assert matching.status_code == 200
    assert matching.json() == {"ready": True, "release_sha": RELEASE_SHA}
    assert unnamed.status_code == 503
    assert wrong.status_code == 503


def test_health_without_a_release_sha_is_ready_only_in_test_mode(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ready": True, "release_sha": None}
