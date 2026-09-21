"""AD01's public Worker-type and stored-Stage contract."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import build_shipped_registry

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

SHIPPED_REGISTRY = build_shipped_registry()

def _app(tmp_path: Path) -> FastAPI:
    db_path = tmp_path / "worker-type-stage.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, build_clock(config), conn_factory)


def test_http_contract_uses_only_worker_type_and_stage(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        missing = client.post("/api/tickets", json={"title": "Missing"})
        assert missing.status_code == 400
        unknown = client.post("/api/tickets", json={"title": "Unknown", "worker_type": "bogus"})
        assert unknown.status_code == 400
        made = client.post(
            "/api/tickets",
            json={"title": "Coding", "worker_type": "coding", "kickoff_note": "k"},
        )
        assert made.status_code == 200, made.json()
        assert made.json()["worker_type"] == "coding"
        assert made.json()["stage"] == "needs_brief"
        assert "ticket_type" not in made.json()
        assert "state" not in made.json()
        manifest = client.get("/api/worker-types")
        assert manifest.status_code == 200
        assert [item["worker_type"] for item in manifest.json()["worker_types"]] == list(
            SHIPPED_REGISTRY.registered_worker_types()
        )
        assert client.get("/api/ticket-types").status_code == 404

        list_parameters = {
            item["name"]
            for item in client.get("/openapi.json").json()["paths"]["/api/tickets"]["get"][
                "parameters"
            ]
        }
        assert "stage" in list_parameters
        assert "worker_type" not in list_parameters
