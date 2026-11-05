"""Small HTTP application factory for persisted test databases."""

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app

ALLOWED_LOGIN = "schedule-owner@example.com"
CANONICAL_ORIGIN = "https://panels.tailnet.ts.net"


def _app(conn: Connection, *, hosted: bool = False) -> FastAPI:
    database = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    env = {"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(database)}
    if hosted:
        env.update(
            PLAN_TRUSTED_INGRESS_PROVIDER="tailscale",
            PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN=ALLOWED_LOGIN,
            PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN=CANONICAL_ORIGIN,
        )
    config = load_config(path=None, env=env)
    return create_app(config, build_clock(config), lambda: connect(str(database)))
