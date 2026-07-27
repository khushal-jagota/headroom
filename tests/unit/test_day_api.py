"""Day route wake regressions."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Run me today.",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        return ticket.id
    finally:
        conn.close()


def test_day_add_and_removal_are_idempotent_and_land_on_the_day(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        response = client.post("/api/day/today/tickets", json={"ticket_id": ticket_id})
        duplicate = client.post("/api/day/today/tickets", json={"ticket_id": ticket_id})
        removed = client.delete(f"/api/day/today/tickets/{ticket_id}")
        absent = client.delete(f"/api/day/today/tickets/{ticket_id}")

    assert response.status_code == 200, response.json()
    assert duplicate.status_code == 200, duplicate.json()
    assert removed.status_code == 200, removed.json()
    assert absent.status_code == 200, absent.json()
    assert response.json()["tickets"][0]["id"] == ticket_id
    assert duplicate.json()["tickets"][0]["id"] == ticket_id
    assert removed.json()["tickets"] == []
    assert absent.json()["tickets"] == []
