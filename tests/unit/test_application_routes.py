"""The served application contains no development-server ingress."""

from __future__ import annotations

import sqlite3

from planner.core.clock import Clock
from planner.core.config import Config
from planner.core.server import create_app


def test_application_has_no_development_routes(
    cfg: Config,
    fake_clock: Clock,
    tmp_db: sqlite3.Connection,
) -> None:
    app = create_app(cfg, fake_clock, lambda: tmp_db)

    served_paths = {
        route.path
        for route in app.routes
        if isinstance(getattr(route, "path", None), str)
    }

    assert not {path for path in served_paths if path.startswith("/dev/")}
