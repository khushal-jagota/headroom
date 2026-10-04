"""Manual and agent HTTP creation share the disabled model boundary."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.support.probe import seed_probe_worker_type

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.worker_types.configuration import load_worker_runtime_definitions


@pytest.mark.parametrize("actor", ["owner", "chief", "worker"])
@pytest.mark.parametrize("explicit", [False, True])
def test_http_ticket_creation_refuses_disabled_model_without_partial_writes(
    tmp_path: Path,
    actor: str,
    explicit: bool,
) -> None:
    db_path = tmp_path / "creation.db"
    conn = connect(str(db_path))
    create_schema(conn)
    seed_probe_worker_type(conn)
    load_worker_runtime_definitions(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-07-10T12:00:00+01:00",
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        worker_ticket = client.post(
            "/api/tickets",
            json={"title": "Existing worker", "worker_type": "probe"},
        )
        assert worker_ticket.status_code == 200, worker_ticket.text
        headers: dict[str, str] = {}
        if actor != "owner":
            headers["X-Plan-Actor"] = actor
        if actor == "worker":
            headers["X-Plan-Ticket-ID"] = worker_ticket.json()["id"]
        body = {"title": "Refused new work", "worker_type": "probe"}
        if explicit:
            body.update(employee_backend="hermes", employee_launch_model="probe-model")
        conn = connect(str(db_path))
        try:
            write_model_enablement(conn, ConversationBackendKey.hermes, "probe-model", False)
            conn.commit()
            before = tuple(conn.iterdump())
            response = client.post("/api/tickets", json=body, headers=headers)
            assert response.status_code == 400, response.text
            error = response.json()["error"]
            assert error["code"] == "validation"
            assert "disabled" in error["message"]
            assert "Choose an enabled model" in error["message"]
            assert tuple(conn.iterdump()) == before
            body.update(employee_backend="hermes", employee_launch_model="enabled-model")
            enabled = client.post("/api/tickets", json=body, headers=headers)
            assert enabled.status_code == 200, enabled.text
            assert enabled.json()["employee_backend"] == "hermes"
            assert enabled.json()["employee_launch_model"] == "enabled-model"
            assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 2
            assert conn.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 2
            historical = conn.execute(
                "SELECT employee_launch_model FROM tickets WHERE id = ?",
                (worker_ticket.json()["id"],),
            ).fetchone()
            assert historical["employee_launch_model"] == "probe-model"
        finally:
            conn.close()
