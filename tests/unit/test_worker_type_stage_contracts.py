"""AD01's public Worker-type and stored-Stage contract."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
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
