"""t_tt05 — `panels worker my-ticket` returns the ticket's worker specialist.

The by-session endpoint resolves the worker name from the ticket's TYPE through the
registry, so a coding ticket routes to ``panels-worker-coding`` and a probe ticket
(exercised via the fixture registry) routes to ``probe-worker``. This is the agent's
self-routing cue — computed, not hardcoded.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "worker-my-ticket.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_GATEWAY_ADAPTER": "fake",
        "PLAN_DB_PATH": str(db_path),
    }
    config = load_config(path=None, env=env)
    clock = build_clock(config)

    def conn_factory():
        return connect(str(db_path))

    app = create_app(config, clock, build_adapters(config), conn_factory)
    return app, db_path


def _bind_session(db_path: Path, ticket_id: str, session_key: str) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tickets SET chat_session_key = ? WHERE id = ?",
            (session_key, ticket_id),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def probe_installed() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


def test_my_ticket_returns_coding_specialist(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/tickets",
            json={
                "title": "Coding work",
                "worker_type": "coding",
                "kickoff_note": "k",
            },
        )
        assert created.status_code == 200, created.text
        ticket_id = created.json()["id"]
        _bind_session(db_path, ticket_id, "sess_coding")

        response = client.get("/api/tickets/by-session/sess_coding")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == ticket_id
    assert body["worker"] == "panels-worker-coding"


def test_my_ticket_returns_probe_specialist(
    tmp_path: Path, probe_installed: None
) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/tickets",
            json={
                "title": "Probe work",
                "worker_type": "probe",
                "kickoff_note": "k",
            },
        )
        assert created.status_code == 200, created.text
        ticket_id = created.json()["id"]
        _bind_session(db_path, ticket_id, "sess_probe")

        response = client.get("/api/tickets/by-session/sess_probe")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == ticket_id
    assert body["worker"] == "probe-worker"
