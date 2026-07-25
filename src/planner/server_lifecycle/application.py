"""Replaceable application-process entry point owned by the Panels supervisor."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from types import FrameType

import uvicorn

from planner.core.clock import build_clock
from planner.core.config import HOST, load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.core.sse import close_open_change_streams


class ChangeStreamClosingServer(uvicorn.Server):
    """Close the browser's change streams first, then shut down normally.

    Uvicorn waits for open connections to finish before it stops, and never cuts an
    HTTP response short. A change stream is idle nearly all the time and only ends when
    its browser leaves, so left alone it would hold the whole shutdown open.
    """

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        close_open_change_streams()
        super().handle_exit(sig, frame)


def run_application_process() -> None:
    """Compose and run the existing application from its imported source root."""
    launch_root = Path(__file__).resolve().parents[3]
    os.chdir(launch_root)
    config = load_config(os.environ.get("PLAN_CONFIG_PATH", str(launch_root / "config.yaml")))
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
        server_config = uvicorn.Config(app, fd=int(listener_fd))
    else:
        server_config = uvicorn.Config(app, host=HOST, port=config.port)
    ChangeStreamClosingServer(server_config).run()


if __name__ == "__main__":
    run_application_process()
