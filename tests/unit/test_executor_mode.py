"""Application startup selects Mac routing from its ordinary configuration."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app


@pytest.mark.parametrize("executor_id", [None, "owner-mac"])
def test_executor_health_uses_configured_id_without_a_mode_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, executor_id: str | None
) -> None:
    # Opposing ambient flags must not decide whether Mac routing is enabled.
    monkeypatch.setenv("PLAN_EXECUTOR_MODE", "remote" if executor_id is None else "local")
    configuration = tmp_path / "config.yaml"
    configuration.write_text("{}\n" if executor_id is None else "executor_id: owner-mac\n")
    config = load_config(
        str(configuration),
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
            "PLAN_DISPATCH_ENABLED": "0",
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(config.db_path),
    )
    with TestClient(app) as client:
        health = client.get("/api/executor/health")
        assert health.status_code == 200
        assert health.json()["mode"] == ("local" if executor_id is None else "remote")
        assert health.json()["connection_state"] == "disconnected"
        assert health.json()["reconciliation_ready"] is False
