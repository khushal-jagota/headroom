"""The rule that keeps a request's database work off the server's one event loop.

A query blocks the thread it runs on: a write for up to the busy timeout while it waits on
the SQLite lock, a read for as long as it reads. On the event loop that thread is every
request's thread. So a route takes the per-request connection only as a plain function,
which FastAPI runs on a worker thread. An ``async def`` route that takes it fails here.
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

from fastapi.dependencies.models import Dependant
from fastapi.routing import iter_route_contexts

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
from planner.core.server import create_app
from planner.tickets.api import db_conn


def _calls(dependant: Dependant) -> Iterator[object]:
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _calls(dependency)


def test_no_async_route_takes_the_request_connection(tmp_path: Path) -> None:
    db_path = tmp_path / "planning.db"
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)})

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(config, build_clock(config), conn_factory)
    # Included routers sit in the table as one entry each, so read the routes through them.
    # Starlette's own routes, such as the API docs, have no dependencies to read.
    routes_on_the_connection = [
        route
        for route in iter_route_contexts(app.routes)
        if (dependant := getattr(route, "dependant", None)) is not None
        and db_conn in _calls(dependant)
    ]
    # The rule means nothing if the walk stops finding the routes it governs.
    assert len(routes_on_the_connection) > 80

    on_the_loop = sorted(
        f"{sorted(route.methods or ())} {route.path} {route.endpoint.__qualname__}"
        for route in routes_on_the_connection
        if inspect.iscoroutinefunction(route.endpoint)
    )
    assert on_the_loop == []
