"""Application startup selects Mac routing from its ordinary configuration."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import _with_native_build_1_fields, create_app


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
        if executor_id is None:
            assert health.json() == {"mode": "local", "connection_state": "disconnected"}
            return
        assert health.json() == {
            "mode": "remote",
            "presence": "never",
            "since_ms": None,
            "identity": None,
            "last_contact_at_ms": None,
            "refusal": None,
            "queue_depth": None,
            "oldest_item_age_ms": None,
            "admission_stopped": False,
            "connection_state": "disconnected",
        }


def test_present_mac_reads_as_known_activity_to_native_build_1() -> None:
    # Native build 1 decodes connection_state as required, and shows the Mac's activity
    # only when it is "ready" and connection_generation is above 0. Protocol 22 keeps
    # the generation at 0, so without these fields every Mac Ticket reads "unknown".
    identity = {"executor_incarnation": "mac-1", "connection_generation": 0}
    present = _with_native_build_1_fields(
        {"mode": "remote", "presence": "present", "identity": identity}
    )
    assert present["connection_state"] == "ready"
    assert present["identity"] == {"executor_incarnation": "mac-1", "connection_generation": 1}
    asleep = _with_native_build_1_fields(
        {"mode": "remote", "presence": "asleep", "identity": identity}
    )
    assert asleep["connection_state"] == "disconnected"
    assert asleep["identity"] == identity
