"""t_tt03 — GET /api/worker-types serves the registry's manifests.

Production serves coding, new_worker, then exploration. With the test registry installed
it serves coding, new_worker, exploration, then probe in registration order. The JSON
response round-trips unchanged because it is the single manifest source consumed by the
CLI and web.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.worker_types.configuration import PRODUCTION_WORKER_TYPE_REGISTRY


@pytest.fixture
def app(tmp_path: Path):
    db_path = tmp_path / "manifest.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_GATEWAY_ADAPTER": "fake", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), build_adapters(config), conn_factory)


@pytest.fixture
def probe_installed() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


def test_production_serves_all_shipped_worker_types(app) -> None:
    with TestClient(app) as client:
        served = client.get("/api/worker-types").json()
    assert served == {
        "worker_types": [
            PRODUCTION_WORKER_TYPE_REGISTRY.manifest("coding"),
            PRODUCTION_WORKER_TYPE_REGISTRY.manifest("new_worker"),
            PRODUCTION_WORKER_TYPE_REGISTRY.manifest("exploration"),
        ]
    }


def test_coding_entry_json_roundtrips(app) -> None:
    with TestClient(app) as client:
        served = client.get("/api/worker-types").json()
    assert json.loads(json.dumps(served)) == served
    assert served["worker_types"][0]["worker_type"] == "coding"


def test_installed_probe_appears_after_shipped_worker_types(app, probe_installed: None) -> None:
    with TestClient(app) as client:
        served = client.get("/api/worker-types").json()
    assert [m["worker_type"] for m in served["worker_types"]] == [
        "coding",
        "new_worker",
        "exploration",
        "probe",
    ]
    assert served["worker_types"][0] == PRODUCTION_WORKER_TYPE_REGISTRY.manifest("coding")
