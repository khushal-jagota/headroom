"""The general Send Message API resolves owners and keeps delivery semantics."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True)
    with connect(str(db_path)) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-08-24T12:00:00",
        },
    )
    return (
        create_app(
            config,
            build_clock(config),
            lambda: connect(str(db_path)),
            conversation_system_for_test=InMemoryConversationSystem(),
        ),
        db_path,
    )


def _send(
    client: TestClient,
    target: dict[str, str],
    message: str = "Hello",
    *,
    headers: dict[str, str] | None = None,
) -> Any:
    return client.post(
        "/api/messages/send",
        json={"target": target, "message": message},
        headers=headers or {},
    )


def _create_ticket(client: TestClient) -> str:
    response = client.post(
        "/api/tickets",
        json={"title": "Message target", "worker_type": "coding"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _create_item(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/items", json={"title": "Message target", "project_id": "project_vylo"}
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_every_owner_selector_resolves_and_first_send_starts(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        ticket_id = _create_ticket(client)
        item = _create_item(client)

        ticket = _send(client, {"type": "ticket", "id": ticket_id})
        chief = _send(client, {"type": "chief"})
        sprint_item = _send(client, {"type": "sprint_item", "id": item["id"]})

        chief_conversation_id = str(chief.json()["conversation_id"])
        app.state.conversation_system.complete_running_turn(chief_conversation_id)
        with connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO agents(agent_key, conversation_id) VALUES (?, ?)",
                ("reviewer", chief_conversation_id),
            )
            conn.commit()
        agent = _send(client, {"type": "agent", "id": "reviewer"})

    assert ticket.json() == {
        "target": {"type": "ticket", "id": ticket_id},
        "resolved_destination": {"type": "ticket", "id": ticket_id},
        "conversation_id": ticket.json()["conversation_id"],
        "fate": "started",
    }
    assert chief.json()["resolved_destination"] == {
        "type": "agent",
        "id": "chief_of_staff",
    }
    assert chief.json()["fate"] == "started"
    assert sprint_item.json()["resolved_destination"] == {
        "type": "agent",
        "id": item["supervisor"]["agent_key"],
    }
    assert sprint_item.json()["fate"] == "started"
    assert agent.json() == {
        "target": {"type": "agent", "id": "reviewer"},
        "resolved_destination": {"type": "agent", "id": "reviewer"},
        "conversation_id": chief_conversation_id,
        "fate": "started",
    }


def test_busy_delivery_returns_canonical_queue_fate_without_internal_position(
    tmp_path: Path,
) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        first = _send(client, {"type": "chief"}, "First")
        queued = _send(client, {"type": "chief"}, "Second")

    assert first.json()["fate"] == "started"
    assert queued.json()["fate"] == "queued"
    assert "queue_position" not in queued.json()
    assert queued.json()["conversation_id"] == first.json()["conversation_id"]


def test_stale_agent_pointer_returns_the_canonical_refusal(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents(agent_key, conversation_id) VALUES (?, ?)",
            ("stale-agent", "c_missing"),
        )
        conn.commit()

    with TestClient(app) as client:
        response = _send(client, {"type": "agent", "id": "stale-agent"})

    assert response.status_code == 200, response.text
    assert response.json()["fate"] == "refused"
    assert response.json()["refusal_reason"] == "no_such_conversation"
    assert response.json()["conversation_id"] == "c_missing"


def test_arbitrary_agent_without_a_usable_conversation_has_an_actionable_error(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents(agent_key, conversation_id) VALUES (?, NULL)",
            ("idle-agent",),
        )
        conn.commit()

    with TestClient(app) as client:
        idle = _send(client, {"type": "agent", "id": "idle-agent"})
        missing = _send(client, {"type": "agent", "id": "unknown-agent"})

    assert idle.status_code == 400, idle.text
    assert idle.json()["error"] == {
        "code": "validation",
        "message": "agent has no current conversation and no start configuration",
        "detail": {"agent_key": "idle-agent"},
    }
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["message"] == "agent not found"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"message": "hello"}, "target must be an object"),
        ({"target": {"type": "other"}, "message": "hello"}, "target type must be"),
        ({"target": {"type": "chief", "id": "x"}, "message": "hello"}, "does not take"),
        ({"target": {"type": "ticket"}, "message": "hello"}, "target id is required"),
        ({"target": {"type": "chief"}, "message": "  "}, "must not be empty"),
    ],
)
def test_api_rejects_invalid_targets_and_empty_messages(
    tmp_path: Path, body: dict[str, Any], message: str
) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        response = client.post("/api/messages/send", json=body)

    assert response.status_code == 400, response.text
    assert message in response.json()["error"]["message"]


def test_request_identity_becomes_an_ordinary_sender_label(tmp_path: Path) -> None:
    app, _ = _app(tmp_path)
    with TestClient(app) as client:
        ticket_id = _create_ticket(client)
        item = _create_item(client)
        direct = _send(client, {"type": "ticket", "id": ticket_id}, "Direct")
        worker = _send(
            client,
            {"type": "sprint_item", "id": item["id"]},
            "Worker",
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_sender"},
        )

        system = app.state.conversation_system
        direct_observations = system.observations(direct.json()["conversation_id"])
        worker_observations = system.observations(worker.json()["conversation_id"])

    assert [
        observation.sender_label
        for observation in direct_observations
        if observation.kind is InMemoryConversationObservationKind.prompt_delivered
    ] == ["You"]
    assert [
        observation.sender_label
        for observation in worker_observations
        if observation.kind is InMemoryConversationObservationKind.prompt_delivered
    ] == ["Ticket t_sender"]
