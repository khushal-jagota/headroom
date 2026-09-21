"""Feedback inbox lifecycle, projection, permissions, and CLI."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection
from typing import Any, cast

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.cli.main import main as cli_main
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.feedback.logic import require_page_context


@pytest.fixture
def feedback_client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = tmp_path / "feedback.db"
    with connect(str(db_path)) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-09-11T12:00:00+02:00",
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        yield client


def _create_note(client: TestClient, text: str, **context: str) -> dict[str, Any]:
    response = client.post("/api/feedback", json={"text": text, **context})
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _create_ticket(
    client: TestClient, title: str, *, sprint_item_id: str | None = None
) -> dict[str, Any]:
    body: dict[str, Any] = {"worker_type": "coding", "title": title}
    if sprint_item_id is not None:
        body["sprint_item_id"] = sprint_item_id
    response = client.post("/api/tickets", json=body)
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_capture_list_use_dismiss_reopen_and_ticket_deletion(
    feedback_client: TestClient,
) -> None:
    first = _create_note(
        feedback_client,
        "Tighten the heading",
        page_address="#/workspace",
        page_label="Workspace",
    )
    second = _create_note(feedback_client, "Explain the empty state")
    ticket = _create_ticket(feedback_client, "Improve the workspace")

    listed = feedback_client.get("/api/feedback")
    assert listed.status_code == 200
    assert listed.json() == {
        "open_count": 2,
        "open": [second, first],
        "handled_groups": [],
    }
    assert feedback_client.get("/api/feedback/count").json() == {"open_count": 2}

    used = feedback_client.post(
        "/api/feedback/use",
        json={"feedback_ids": [first["id"]], "ticket_id": ticket["id"]},
    )
    dismissed = feedback_client.post(f"/api/feedback/{second['id']}/dismiss")
    assert used.status_code == 200, used.text
    assert dismissed.status_code == 200, dismissed.text

    handled = feedback_client.get("/api/feedback").json()
    assert handled["open_count"] == 0
    assert handled["handled_groups"][0]["ticket"] == {
        "id": ticket["id"],
        "title": "Improve the workspace",
            "stage": "needs_brief",
            "ticket_status": "awaiting_approval",
            "awaiting_reply": False,
            "awaiting_approval": True,
            "awaiting_agent_approval": False,
            "assigned": True,
            "agent_state": "idle",
        }
    assert [note["id"] for note in handled["handled_groups"][0]["notes"]] == [
        first["id"]
    ]
    assert handled["handled_groups"][-1]["ticket"] is None
    assert handled["handled_groups"][-1]["notes"][0]["id"] == second["id"]

    reopened = feedback_client.post(f"/api/feedback/{second['id']}/reopen")
    assert reopened.status_code == 200
    assert reopened.json()["state"] == "open"
    assert reopened.json()["handled_at"] is None

    deleted = feedback_client.delete(f"/api/tickets/{ticket['id']}")
    assert deleted.status_code == 200, deleted.text
    after_delete = feedback_client.get("/api/feedback").json()
    assert len(after_delete["handled_groups"]) == 1
    assert after_delete["handled_groups"][0]["ticket"] is None
    assert after_delete["handled_groups"][0]["notes"][0]["ticket_id"] is None


def test_multi_note_use_is_atomic(feedback_client: TestClient) -> None:
    note = _create_note(feedback_client, "Keep me open")
    ticket = _create_ticket(feedback_client, "Atomic destination")

    failed = feedback_client.post(
        "/api/feedback/use",
        json={"feedback_ids": [note["id"], "feedback_missing"], "ticket_id": ticket["id"]},
    )

    assert failed.status_code == 404
    assert feedback_client.get("/api/feedback").json()["open"] == [note]


@pytest.mark.parametrize(
    ("page_address", "page_label"),
    (("#/workspace", None), (None, "Workspace"), ("", "Workspace"), ("#/workspace", " ")),
)
def test_feedback_page_context_requires_two_meaningful_values_or_neither(
    page_address: str | None, page_label: str | None
) -> None:
    with pytest.raises(PlannerError) as raised:
        require_page_context(page_address, page_label)
    assert raised.value.code is ErrorCode.validation

    assert require_page_context(None, None) == (None, None)
    assert require_page_context("#/workspace", "Workspace") == (
        "#/workspace",
        "Workspace",
    )


@pytest.mark.parametrize(
    "page_address",
    (
        "javascript:alert(1)",
        "https://example.com/",
        "/#/workspace",
        "#/workspace#other",
        "#/workspace\n",
        "#/",
    ),
)
def test_feedback_page_context_rejects_non_panels_addresses(
    feedback_client: TestClient, page_address: str
) -> None:
    response = feedback_client.post(
        "/api/feedback",
        json={"text": "Unsafe context", "page_address": page_address, "page_label": "Page"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_feedback_use_admission_runs_inside_the_write_transaction(
    feedback_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    note = _create_note(feedback_client, "Keep authorization atomic")
    ticket = _create_ticket(feedback_client, "Authorization destination")
    observed: list[bool] = []

    def admit(conn: Connection, _caller: object, _target: object) -> None:
        observed.append(conn.in_transaction)

    # The one rule replaced require_feedback_use here. What this proves is unchanged: the
    # admission runs inside the writer's transaction, not before it.
    monkeypatch.setattr("planner.feedback.api.require_above_or_self", admit)
    response = feedback_client.post(
        "/api/feedback/use",
        json={"feedback_ids": [note["id"]], "ticket_id": ticket["id"]},
    )

    assert response.status_code == 200, response.text
    assert observed == [True]


def test_feedback_write_permissions_follow_direct_worker_and_supervisor_scope(
    feedback_client: TestClient,
) -> None:
    first_item = feedback_client.post(
        "/api/items", json={"title": "First", "project_id": "project_vylo"}
    ).json()
    second_item = feedback_client.post(
        "/api/items", json={"title": "Second", "project_id": "project_vylo"}
    ).json()
    own_ticket = _create_ticket(
        feedback_client, "Own child", sprint_item_id=str(first_item["id"])
    )
    other_ticket = _create_ticket(
        feedback_client, "Other child", sprint_item_id=str(second_item["id"])
    )
    notes = [_create_note(feedback_client, f"Note {index}") for index in range(5)]
    worker_headers = {
        "X-Plan-Actor": "worker",
        "X-Plan-Ticket-ID": str(own_ticket["id"]),
    }
    supervisor_headers = {
        "X-Plan-Actor": "sprint_item_supervisor",
        "X-Plan-Sprint-Item-ID": str(first_item["id"]),
    }

    assert feedback_client.get("/api/feedback", headers=worker_headers).status_code == 200
    assert feedback_client.post(
        "/api/feedback", json={"text": "Forbidden"}, headers=worker_headers
    ).json()["error"]["code"] == "agent_forbidden"
    assert feedback_client.post(
        "/api/feedback/use",
        json={"feedback_ids": [notes[0]["id"]], "ticket_id": own_ticket["id"]},
        headers=worker_headers,
    ).status_code == 200
    assert feedback_client.post(
        "/api/feedback/use",
        json={"feedback_ids": [notes[1]["id"]], "ticket_id": other_ticket["id"]},
        headers=worker_headers,
    ).json()["error"]["code"] == "agent_forbidden"
    assert feedback_client.post(
        "/api/feedback/use",
        json={"feedback_ids": [notes[2]["id"]], "ticket_id": own_ticket["id"]},
        headers=supervisor_headers,
    ).status_code == 200
    assert feedback_client.post(
        "/api/feedback/use",
        json={"feedback_ids": [notes[3]["id"]], "ticket_id": other_ticket["id"]},
        headers=supervisor_headers,
    ).json()["error"]["code"] == "agent_forbidden"


def test_feedback_cli_lists_and_uses_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def send(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        calls.append((method, path, kwargs))
        if method == "GET":
            return {
                "open_count": 1,
                "open": [
                    {
                        "id": "feedback_one",
                        "text": "Inbox note",
                        "page_label": "Workspace",
                        "created_at": 1_789_120_800,
                    }
                ],
                "handled_groups": [],
            }
        return {"notes": [{"id": "feedback_one"}]}

    monkeypatch.setattr("planner.cli.main.http.send", send)
    runner = CliRunner()

    listed = runner.invoke(cli_main, ["feedback", "list"])
    used = runner.invoke(
        cli_main,
        ["feedback", "use", "--ticket", "t_target", "feedback_one", "--json"],
    )

    assert listed.exit_code == 0, listed.output
    assert used.exit_code == 0, used.output
    assert "feedback_one" in listed.output
    assert "Workspace" in listed.output
    assert "Inbox note" in listed.output
    assert "2026-09-" in listed.output
    assert calls == [
        (
            "GET",
            "/api/feedback",
            {"as_json": False},
        ),
        (
            "POST",
            "/api/feedback/use",
            {
                "as_json": True,
                "json_body": {
                    "feedback_ids": ["feedback_one"],
                    "ticket_id": "t_target",
                },
            },
        ),
    ]
