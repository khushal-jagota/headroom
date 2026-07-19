"""S3 §6 / §9.4 — `panels worker my-ticket` identity resolution.

The CLI reads `PLAN_TICKET_ID` (the child's spawn env, set by Panels flag-on) FIRST and
resolves the ticket through the by-ticket-id worker-self route. When `PLAN_TICKET_ID` is
absent it FALLS BACK to the existing Hermes-env resolution (the flag-off legacy shared-child
path, Collision #1 (a) — the transitional fallback the owner authorized). When neither
identity is present it fails validation.

Plus the server-side by-ticket-id worker-self route: it resolves the ticket by id and applies
the ownership validation the plain detail read lacks — an ambiguously-owned durable session is
rejected. Fakes only; no real Hermes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.cli import http as cli_http
from planner.cli.main import main as cli_main
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

# --- server: the by-ticket-id worker-self route ------------------------------


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "worker-cli-identity.db"
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
            "UPDATE tickets SET employee_session_id = ? WHERE id = ?",
            (session_key, ticket_id),
        )
        conn.commit()
    finally:
        conn.close()


def test_worker_self_route_returns_detail_and_worker(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/tickets",
            json={"title": "Coding work", "worker_type": "coding", "kickoff_note": "k"},
        )
        assert created.status_code == 200, created.text
        ticket_id = created.json()["id"]
        _bind_session(db_path, ticket_id, "sess_coding")

        response = client.get(f"/api/tickets/{ticket_id}/worker-self")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == ticket_id
    # The same detail shape (ticket_detail + worker specialist) the by-session route returns.
    assert body["worker"] == "panels-worker-coding"
    assert body["stage"]
    assert body["title"] == "Coding work"


def test_worker_self_route_resolves_ticket_with_no_bound_session(tmp_path: Path) -> None:
    # A never-run ticket (no durable session yet) still resolves by id — the ownership
    # validation only fires when a session IS bound.
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/api/tickets",
            json={"title": "Fresh", "worker_type": "coding", "kickoff_note": "k"},
        )
        ticket_id = created.json()["id"]
        response = client.get(f"/api/tickets/{ticket_id}/worker-self")

    assert response.status_code == 200, response.text
    assert response.json()["id"] == ticket_id


def test_worker_self_route_ownership_validation_rejects_ambiguous_session(
    tmp_path: Path,
) -> None:
    # Two tickets bound to the SAME durable session id — a corrupt duplicate. The worker-self
    # route must reject the ambiguously-owned session (the plain /tickets/{id} read does not).
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
        _bind_session(db_path, first["id"], "duplicate_session")
        _bind_session(db_path, second["id"], "duplicate_session")

        response = client.get(f"/api/tickets/{first['id']}/worker-self")

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation"


def test_worker_self_route_missing_ticket_is_not_found(tmp_path: Path) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get("/api/tickets/t_missing/worker-self")
    assert response.status_code == 404, response.text


# --- CLI: PLAN_TICKET_ID first, Hermes-env fallback --------------------------


class _RecordingSend:
    """Capture the path `worker_my_ticket` GETs, returning a canned detail body."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.paths: list[str] = []
        self._body = body

    def __call__(self, method: str, path: str, **_kwargs: Any) -> Any:
        self.paths.append(path)
        return self._body


_DETAIL_BODY = {
    "id": "t_abc",
    "stage": "coding",
    "priority": "P1",
    "title": "Do the thing",
    "worker": "panels-worker-coding",
}


@pytest.fixture
def clear_identity_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("PLAN_TICKET_ID", raising=False)
    monkeypatch.delenv("HERMES_UI_SESSION_ID", raising=False)
    monkeypatch.delenv("HERMES_SESSION_KEY", raising=False)
    yield


def test_worker_my_ticket_resolves_from_plan_ticket_id(
    monkeypatch: pytest.MonkeyPatch, clear_identity_env: None
) -> None:
    monkeypatch.setenv("PLAN_TICKET_ID", "t_abc")
    # Even if a stale Hermes env is also present, PLAN_TICKET_ID wins.
    monkeypatch.setenv("HERMES_UI_SESSION_ID", "stale_live")
    recorder = _RecordingSend(_DETAIL_BODY)
    monkeypatch.setattr(cli_http, "send", recorder)

    result = CliRunner().invoke(cli_main, ["worker", "my-ticket", "--json"])

    assert result.exit_code == 0, result.output
    assert recorder.paths == ["/api/tickets/t_abc/worker-self"]


def test_worker_my_ticket_falls_back_to_hermes_env_when_no_plan_ticket_id(
    monkeypatch: pytest.MonkeyPatch, clear_identity_env: None
) -> None:
    # No PLAN_TICKET_ID (legacy shared child, flag-off) but the per-turn Hermes live-session
    # env is present -> the CLI falls back to the by-live-session route (Collision #1 (a)).
    monkeypatch.setenv("HERMES_UI_SESSION_ID", "live_xyz")
    recorder = _RecordingSend(_DETAIL_BODY)
    monkeypatch.setattr(cli_http, "send", recorder)

    result = CliRunner().invoke(cli_main, ["worker", "my-ticket", "--json"])

    assert result.exit_code == 0, result.output
    assert recorder.paths == ["/api/tickets/by-live-session/live_xyz"]


def test_worker_my_ticket_falls_back_to_employee_session_env(
    monkeypatch: pytest.MonkeyPatch, clear_identity_env: None
) -> None:
    monkeypatch.setenv("HERMES_SESSION_KEY", "sess_key")
    recorder = _RecordingSend(_DETAIL_BODY)
    monkeypatch.setattr(cli_http, "send", recorder)

    result = CliRunner().invoke(cli_main, ["worker", "my-ticket", "--json"])

    assert result.exit_code == 0, result.output
    assert recorder.paths == ["/api/tickets/by-employee-session/sess_key"]


def test_worker_my_ticket_no_identity_fails_validation(
    monkeypatch: pytest.MonkeyPatch, clear_identity_env: None
) -> None:
    # Neither PLAN_TICKET_ID nor any Hermes env -> validation failure, no HTTP call.
    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no HTTP request should be issued without an identity")

    monkeypatch.setattr(cli_http, "send", _explode)

    result = CliRunner().invoke(cli_main, ["worker", "my-ticket", "--json"])

    assert result.exit_code != 0
