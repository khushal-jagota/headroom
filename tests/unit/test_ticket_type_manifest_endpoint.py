"""t_tt03 — GET /api/ticket-types serves the registry's manifests.

Production is coding-only, so the endpoint returns exactly one entry (coding's
serialized manifest). With a test registry installed (probe), it returns both, in
type_ids() insertion order (coding, probe). The served coding entry is asserted as
the exact serialize_definition dict and to JSON-round-trip — this is the single
source the CLI (and later the web) consume, so its shape is pinned here.
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
from planner.ticket_types.coding import CODING_DEFINITION
from planner.ticket_types.logic.manifest import serialize_definition
from planner.ticket_types.new_worker import NEW_WORKER_DEFINITION


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


def test_production_serves_coding_and_new_worker(app) -> None:
    with TestClient(app) as client:
        served = client.get("/api/ticket-types").json()
    assert served == {
        "types": [
            serialize_definition(CODING_DEFINITION),
            serialize_definition(NEW_WORKER_DEFINITION),
        ]
    }


def test_coding_entry_json_roundtrips(app) -> None:
    with TestClient(app) as client:
        served = client.get("/api/ticket-types").json()
    assert json.loads(json.dumps(served)) == served
    assert served["types"][0]["type_id"] == "coding"


def test_installed_probe_appears_after_coding(app, probe_installed: None) -> None:
    with TestClient(app) as client:
        served = client.get("/api/ticket-types").json()
    assert [m["type_id"] for m in served["types"]] == ["coding", "probe"]
    assert served["types"][0] == serialize_definition(CODING_DEFINITION)
