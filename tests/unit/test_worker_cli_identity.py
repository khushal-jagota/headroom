"""`panels worker my-ticket` uses only the explicit ACP child Ticket identity."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import build_shipped_registry

from planner.cli import http as cli_http
from planner.cli.main import main as cli_main
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

SHIPPED_REGISTRY = build_shipped_registry()

# --- server: the by-ticket-id worker-self route ------------------------------


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "worker-cli-identity.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(db_path),
    }
    config = load_config(path=None, env=env)
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(config, clock, conn_factory)
    return app, db_path


def _bind_session(db_path: Path, ticket_id: str, session_key: str) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            "INSERT OR IGNORE INTO conversations "
            "(conversation_id, backend_key, model, workspace_folder, access, created_at) "
            "VALUES (?, 'codex', 'model', '/work', 'full', 1)",
            (session_key,),
        )
        conn.execute(
            "INSERT OR IGNORE INTO ticket_conversations (conversation_id, ticket_id) "
            "VALUES (?, ?)",
            (session_key, ticket_id),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
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


def test_worker_self_route_ownership_validation_rejects_ambiguous_session(
    tmp_path: Path,
) -> None:
    # Two active pointers mirror one durable conversation. Its unique history association
    # belongs to the first Ticket, so the second Ticket must reject that foreign ownership.
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

        response = client.get(f"/api/tickets/{second['id']}/worker-self")

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation"


# --- CLI: PLAN_TICKET_ID only ------------------------------------------------


class _RecordingSend:
    """Capture the path `worker_my_ticket` GETs, returning a canned detail body."""

    def __init__(self, body: dict[str, Any]) -> None:
        self.paths: list[str] = []
        self._body = body

    def __call__(self, method: str, path: str, **_kwargs: Any) -> Any:
        self.paths.append(path)
        if path == "/api/worker-types":
            return {"worker_types": [SHIPPED_REGISTRY.manifest("coding")]}
        return self._body


_DETAIL_BODY: dict[str, Any] = {
    "id": "t_abc",
    "worker_type": "coding",
    "stage": "needs_implementation",
    "ticket_status": "agent",
    "priority": "P1",
    "title": "Do the thing",
    "worker": "panels-worker-coding",
    "field_values": {},
    "pending_proposal": None,
    "archived_field_content": "",
}


@pytest.fixture
def clear_identity_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("PLAN_TICKET_ID", raising=False)
    yield


def test_worker_my_ticket_resolves_from_plan_ticket_id(
    monkeypatch: pytest.MonkeyPatch, clear_identity_env: None
) -> None:
    monkeypatch.setenv("PLAN_TICKET_ID", "t_abc")
    recorder = _RecordingSend(_DETAIL_BODY)
    monkeypatch.setattr(cli_http, "send", recorder)

    result = CliRunner().invoke(cli_main, ["worker", "my-ticket", "--json"])

    assert result.exit_code == 0, result.output
    assert recorder.paths == ["/api/tickets/t_abc/worker-self", "/api/worker-types"]


def test_worker_my_ticket_no_identity_fails_validation(
    monkeypatch: pytest.MonkeyPatch, clear_identity_env: None
) -> None:
    # Neither PLAN_TICKET_ID nor any Hermes env -> validation failure, no HTTP call.
    def _explode(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("no HTTP request should be issued without an identity")

    monkeypatch.setattr(cli_http, "send", _explode)

    result = CliRunner().invoke(cli_main, ["worker", "my-ticket", "--json"])

    assert result.exit_code != 0
