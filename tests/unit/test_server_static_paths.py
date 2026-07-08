"""Server static mounts do not depend on the caller's working directory."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def test_static_assets_are_served_when_cwd_has_no_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    monkeypatch.chdir(tmp_path)
    app = create_app(config, clock, adapters, conn_factory)
    with TestClient(app) as client:
        root = client.get("/")
        css = client.get("/assets/app.css")
        favicon = client.get("/static/favicon.ico")

    assert root.status_code == 200
    assert "data-svelte-app" in root.text
    assert css.status_code == 200
    assert ".ticket-page" in css.text
    assert favicon.status_code == 200
