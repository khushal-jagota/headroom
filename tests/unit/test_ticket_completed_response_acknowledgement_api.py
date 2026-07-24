from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.conversation_projection import TicketConversationProjection
from planner.tickets.data import create_ticket

_AGENT = {"X-Plan-Actor": "agent"}


def _make_app(tmp_path: Path) -> tuple[object, Path, str]:
    db_path = tmp_path / "planning-test.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket = create_ticket(
        conn,
        worker_type="coding",
        title="Open me",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    conn.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path, ticket.id


def test_direct_user_acknowledges_completed_response_only_once(tmp_path: Path) -> None:
    app, db_path, ticket_id = _make_app(tmp_path)
    projection = TicketConversationProjection(str(db_path), now=lambda: 2)
    projection.record_activity(ticket_id, "thinking")
    projection.record_activity(ticket_id, "idle")
    projection.record_permission(ticket_id, True)

    with TestClient(app) as client:
        first = client.post(
            f"/api/tickets/{ticket_id}/acknowledge-completed-response"
        )
        repeated = client.post(
            f"/api/tickets/{ticket_id}/acknowledge-completed-response"
        )

    assert first.status_code == 200
    assert first.json() == {"acknowledged": True}
    assert repeated.status_code == 200
    assert repeated.json() == {"acknowledged": False}
    snapshot = projection.read(ticket_id)
    assert snapshot.has_completed_response_awaiting_user is False
    # The acknowledged reply stays remembered as seen.
    assert snapshot.has_completed_response is True
    assert snapshot.has_pending_permission is True


def test_agent_cannot_acknowledge_completed_response(tmp_path: Path) -> None:
    app, _, ticket_id = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket_id}/acknowledge-completed-response",
            headers=_AGENT,
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_acknowledging_a_missing_ticket_returns_not_found(tmp_path: Path) -> None:
    app, _, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.post(
            "/api/tickets/t_missing/acknowledge-completed-response"
        )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
