"""The worker-self route resolves only an explicit Ticket identity."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "worker-my-ticket.db"
    with connect(str(db_path)) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    return create_app(config, build_clock(config), lambda: connect(str(db_path))), db_path


def _bind(db_path: Path, ticket_id: str, session_id: str) -> None:
    with connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (session_id, ticket_id),
        )


@pytest.fixture
def probe_installed() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


@pytest.mark.parametrize(
    ("worker_type", "expected_worker"),
    [("coding", "panels-worker-coding"), ("probe", "probe-worker")],
)
def test_worker_self_returns_type_specialist(
    tmp_path: Path,
    probe_installed: None,
    worker_type: str,
    expected_worker: str,
) -> None:
    app, _db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/tickets",
            json={"title": "Worker task", "worker_type": worker_type, "kickoff_note": "k"},
        )
        ticket_id = created.json()["id"]
        response = client.get(f"/api/tickets/{ticket_id}/worker-self")
    assert response.status_code == 200, response.text
    assert response.json()["worker"] == expected_worker


def test_worker_self_rejects_duplicate_ticket_mirror_ownership(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        first = client.post(
            "/api/tickets",
            json={"title": "First", "worker_type": "coding", "kickoff_note": "k"},
        ).json()
        second = client.post(
            "/api/tickets",
            json={"title": "Second", "worker_type": "coding", "kickoff_note": "k"},
        ).json()
        _bind(db_path, first["id"], "duplicate-session")
        _bind(db_path, second["id"], "duplicate-session")
        response = client.get(f"/api/tickets/{first['id']}/worker-self")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
