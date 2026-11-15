"""Stored evidence crosses HTTP without collecting from the host."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.environments.vps_metrics.api import router
from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.contracts import Sample
from planner.environments.vps_metrics.data import Store
from planner.environments.vps_metrics.runtime import MetricsRuntime


def test_stored_api_limits_and_immutable_process_pages(tmp_path: Path) -> None:
    config = MetricsConfig()
    path = tmp_path / "metrics.sqlite3"
    store = Store(path, config)
    runtime = MetricsRuntime(path, store, None, config)
    runtime.start()
    now = time.time()
    sample: Sample = {
        "timestamp": now,
        "monotonic": 10,
        "boot_id": "boot",
        "expected_interval": 2,
        "core_count": 2,
        "metrics": {"cpu_percent": {"value": 25.0, "unit": "%", "reason": None}},
        "services": [],
        "processes": [
            {
                "identity": {"boot_id": "boot", "pid": pid, "start_ticks": pid},
                "parent_pid": 1,
                "uid": 1000,
                "name": f"process-{pid}",
                "state": "S",
                "service": "unassigned",
                "metrics": {},
            }
            for pid in (2, 3)
        ],
        "process_count": 2,
        "process_availability": "available",
        "process_reason": None,
        "disk_total_members": [],
        "network_total_members": [],
    }
    sequence = store.append(sample)
    app = FastAPI()
    app.state.vps_metrics = runtime
    app.include_router(router, prefix="/api")
    try:
        with TestClient(app) as client:
            latest = client.get("/api/vps-metrics/latest")
            assert latest.status_code == 200
            assert latest.json()["sample"]["metrics"]["cpu_percent"]["value"] == 25
            first = client.get("/api/vps-metrics/processes", params={"limit": 1}).json()
            assert first["total"] == 2
            sample["timestamp"] += 2
            sample["monotonic"] += 2
            sample["processes"] = []
            sample["process_count"] = 0
            assert store.append(sample) != sequence
            second = client.get(
                "/api/vps-metrics/processes",
                params={
                    "sequence": sequence,
                    "offset": first["next_offset"],
                    "limit": 1,
                },
            ).json()
            assert second["processes"][0]["identity"]["pid"] == 3
            assert (
                client.get("/api/vps-metrics/processes", params={"limit": 251}).status_code == 422
            )
            assert (
                client.get("/api/vps-metrics/processes", params={"sequence": 999}).status_code
                == 410
            )
            assert (
                client.get(
                    "/api/vps-metrics/history",
                    params={
                        "signal": "cpu_percent",
                        "start": "nan",
                        "end": now,
                    },
                ).status_code
                == 422
            )
            assert (
                client.get(
                    "/api/vps-metrics/history",
                    params={
                        "signal": "cpu_percent",
                        "start": 0,
                        "end": now,
                    },
                ).status_code
                == 422
            )
            lease = client.post("/api/vps-metrics/activity").json()["active_until"]
            assert now < lease < time.time() + config.activity_seconds + 1
    finally:
        asyncio.run(runtime.stop(deadline=time.monotonic() + 2))


def test_production_lifespan_collects_with_dispatch_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.core.clock import build_clock
    from planner.core.config import load_config
    from planner.core.db import connect
    from planner.core.server import create_app
    from planner.environments.vps_metrics import collectors

    class ControlledCollector:
        def __init__(self, config: MetricsConfig) -> None:
            pass

        def collect(self, *, include_slow: bool) -> Sample:
            return {
                "timestamp": time.time(),
                "monotonic": time.monotonic(),
                "boot_id": "fixture",
                "expected_interval": 15,
                "core_count": 1,
                "metrics": {"cpu_percent": {"value": 12.0, "unit": "%", "reason": None}},
                "services": [],
                "processes": [],
                "process_count": 0,
                "process_availability": "available",
                "process_reason": None,
                "disk_total_members": [],
                "network_total_members": [],
            }

    monkeypatch.setattr(collectors, "LinuxCollector", ControlledCollector)
    database = str(tmp_path / "app.sqlite3")
    config = load_config(
        None,
        env={
            "PLAN_DB_PATH": database,
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
            "PLAN_TEST_MODE": "0",
            "PLAN_DISPATCH_ENABLED": "0",
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatch.lock"),
        },
    )
    app = create_app(config, build_clock(config), lambda: connect(database))
    with TestClient(app) as client:
        deadline = time.monotonic() + 3
        while True:
            result = client.get("/api/vps-metrics/latest")
            assert result.status_code == 200
            if result.json()["sample"] is not None:
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        runtime = app.state.vps_metrics
        assert runtime.owns_collection
        assert result.json()["sample"]["metrics"]["cpu_percent"]["value"] == 12
    assert app.state.vps_metrics is None
    assert not runtime.owns_collection
