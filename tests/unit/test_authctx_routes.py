"""§7.6 route regression: PATCH /api/tickets/{id} validates a carried claim —
a stale/foreign claim is rejected 409 stale_claim and writes nothing, while human
and plain-agent requests (no claim headers) behave exactly as before. Supporting
tests, no §18.3 anchor."""

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
        ticket = create_ticket(conn, title="Patch me.", actor="human", now=0, title_max_chars=200)
    finally:
        conn.close()
    return ticket.id


def _priority(db_path: Path, ticket_id: str) -> str:
    conn = connect(str(db_path))
    try:
        row = conn.execute("SELECT priority FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    finally:
        conn.close()
    return str(row["priority"])


def test_patch_ticket_with_stale_claim_is_409_and_writes_nothing(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P1"},
            headers={"X-Plan-Run-Id": "run_stale", "X-Plan-Claim": "claim_stale"},
        )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "stale_claim"
    assert error["detail"]["reason"] == "none_active"  # no active claim on the ticket
    assert error["detail"]["ticket_id"] == tid
    assert _priority(db_path, tid) == "P3"  # the write never landed


def test_patch_ticket_without_claim_headers_is_unchanged(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket(db_path)
    with TestClient(app) as client:
        human = client.patch(f"/api/tickets/{tid}", json={"priority": "P1"})
        assert human.status_code == 200
        assert human.json()["priority"] == "P1"
        plain_agent = client.patch(
            f"/api/tickets/{tid}",
            json={"priority": "P2"},
            headers={"X-Plan-Actor": "agent"},
        )
    assert plain_agent.status_code == 200
    assert plain_agent.json()["priority"] == "P2"
    assert _priority(db_path, tid) == "P2"
