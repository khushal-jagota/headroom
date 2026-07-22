"""Replaceable application-process entry point owned by the Panels supervisor."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import uvicorn

from planner.core.clock import build_clock
from planner.core.config import HOST, load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def run_application_process() -> None:
    """Compose and run the existing application from its imported source root."""
    launch_root = Path(__file__).resolve().parents[3]
    os.chdir(launch_root)
    config = load_config(str(launch_root / "config.yaml"))
    os.makedirs(os.path.dirname(config.db_path) or ".", exist_ok=True)
    os.makedirs(config.logs_dir, exist_ok=True)
    with connect(config.db_path, config.db_busy_timeout_ms) as bootstrap:
        create_schema(bootstrap)

    clock = build_clock(config)

    def conn_factory() -> sqlite3.Connection:
        return connect(config.db_path, config.db_busy_timeout_ms)

    app = create_app(config, clock, conn_factory)
    listener_fd = os.environ.get("PLAN_SERVER_LISTENER_FD")
    if listener_fd is not None:
        uvicorn.run(app, fd=int(listener_fd))
    else:
        uvicorn.run(app, host=HOST, port=config.port)


if __name__ == "__main__":
    run_application_process()
