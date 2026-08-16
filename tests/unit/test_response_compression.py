"""Responses travel compressed, except the streams that must arrive frame by frame."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _make_app(tmp_path: Path, **extra_environment: str) -> FastAPI:
    db_path = tmp_path / "compression.db"
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


def test_large_api_response_is_gzipped_and_says_so(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        compressed = client.get("/api/worker-types", headers={"Accept-Encoding": "gzip"})
        plain = client.get("/api/worker-types", headers={"Accept-Encoding": "identity"})

    assert compressed.headers["content-encoding"] == "gzip"
    assert int(compressed.headers["content-length"]) < len(plain.content)
    assert "content-encoding" not in plain.headers
    # The client sees the same answer either way.
    assert compressed.json() == plain.json()


def test_small_api_response_is_left_alone(tmp_path: Path) -> None:
    app = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get("/api/meta", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers
    assert response.json()["test_mode"] is True


# The event streams are covered where they are driven as ASGI applications:
# tests/unit/test_server_changes_stream.py.
