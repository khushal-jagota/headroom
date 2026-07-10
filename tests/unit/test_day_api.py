"""Day route wake regressions."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket


def _make_app(tmp_path: Path) -> tuple[object, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(conn, title="Run me today.", actor="human", now=0,
                               title_max_chars=200)
        return ticket.id
    finally:
        conn.close()


def test_add_day_ticket_pokes_readiness_loop_after_successful_add(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    class ReadinessLoopSpy:
        def __init__(self) -> None:
            self.pokes = 0

        def poke(self) -> None:
            self.pokes += 1

    readiness_loop = ReadinessLoopSpy()
    app.state.ticket_readiness_loop = readiness_loop

    with TestClient(app) as client:
        response = client.post("/api/day/today/tickets", json={"ticket_id": ticket_id})
        duplicate = client.post("/api/day/today/tickets", json={"ticket_id": ticket_id})

    assert response.status_code == 200, response.json()
    assert duplicate.status_code == 200, duplicate.json()
    assert response.json()["tickets"][0]["id"] == ticket_id
    assert readiness_loop.pokes == 1
