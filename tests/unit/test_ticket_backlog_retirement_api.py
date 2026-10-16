"""The HTTP controls use the canonical writers and runtime delivery guards."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def test_http_backlog_contact_resume_and_retire(tmp_path: Path) -> None:
    db_path = tmp_path / "isolated.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)})
    conversations = InMemoryConversationSystem()
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=conversations,
    )
    with TestClient(app) as client:
        ticket = client.post(
            "/api/tickets", json={"worker_type": "coding", "title": "Deferred"}
        ).json()
        tid = ticket["id"]
        deferred = client.post(f"/api/tickets/{tid}/backlog", json={})
        assert deferred.status_code == 200, deferred.text
        assert deferred.json()["is_backlogged"] is True
        summary = client.get(
            "/api/tickets",
            params={
                "detail": "summary",
                "is_backlogged": True,
                "worker_type": "coding",
                "limit": 1,
            },
        ).json()
        assert summary["tickets"][0]["id"] == tid
        assert summary["tickets"][0]["is_backlogged"] is True
        contacted = client.post(
            f"/api/tickets/{tid}/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Direct contact while deferred"}],
                "sender_label": "owner",
                "conversation_id": None,
            },
        )
        assert contacted.status_code == 200, contacted.text
        detail = client.get("/api/tickets", params={"detail": "full", "id": tid}).json()
        assert detail["is_backlogged"] and detail["conversation_id"]
        # Direct contact owns runtime traffic. Retirement cannot cancel it silently.
        refused = client.post(f"/api/tickets/{tid}/retire", json={"reason": "Superseded"})
        assert refused.status_code != 200
        resumed = client.post(
            f"/api/tickets/{tid}/resume", json={"expected_record_sha256": detail["record_sha256"]}
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["is_backlogged"] is False
        # A second Ticket with no runtime traffic proves the retirement HTTP result.
        parked = client.post(
            "/api/tickets", json={"worker_type": "coding", "title": "Superseded"}
        ).json()
        retired = client.post(f"/api/tickets/{parked['id']}/retire", json={"reason": "Duplicate"})
        assert retired.status_code == 200, retired.text
        assert retired.json()["stage"] == "cancelled"
        assert retired.json()["pending_proposal"] is None
        assert "retirement" in retired.json()["field_values"]
        assert client.post(f"/api/tickets/{parked['id']}/resume", json={}).status_code != 200


def test_retirement_refuses_held_only_delivery_without_changing_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from planner.conversation.contracts import HeldPrompt
    from planner.conversation.message_content import MessageText

    db_path = tmp_path / "held-only.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
        },
    )
    conversations = InMemoryConversationSystem()
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=conversations,
    )
    with TestClient(app) as client:
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Held delivery",
                "kickoff_note": "Review this brief",
            },
        ).json()
        tid = ticket["id"]
        contacted = client.post(
            f"/api/tickets/{tid}/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Inspect this work"}],
                "sender_label": "owner",
                "conversation_id": None,
            },
        )
        assert contacted.status_code == 200, contacted.text
        before = client.get("/api/tickets", params={"detail": "full", "id": tid}).json()
        assert before["pending_proposal"] is not None
        conversation_id = before["conversation_id"]
        # Exercise the independent held-only runtime state: no active turn owns it,
        # but its retained prompt must not lose its Ticket through retirement.
        running = AsyncMock(return_value=False)
        held = AsyncMock(
            return_value=(
                HeldPrompt(
                    held_prompt_id="held-retirement-guard",
                    content=(MessageText("Retained direct contact"),),
                    sender_label="owner",
                    sender_message_id="held-message",
                    sent_at_unix_milliseconds=1,
                ),
            )
        )
        monkeypatch.setattr(conversations, "is_running", running)
        monkeypatch.setattr(conversations, "held_prompts", held)
        conn = connect(str(db_path))
        try:
            original = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
            refused = client.post(
                f"/api/tickets/{tid}/retire",
                json={"reason": "Superseded", "expected_record_sha256": before["record_sha256"]},
            )
            assert refused.status_code == 409, refused.text
            assert "still owns traffic" in refused.text
            assert (
                dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
                == original
            )
        finally:
            conn.close()
        running.assert_awaited_once_with(conversation_id)
        held.assert_awaited_once_with(conversation_id)
        after = client.get("/api/tickets", params={"detail": "full", "id": tid}).json()
        assert after["record_sha256"] == before["record_sha256"]
        assert after["stage"] == before["stage"]
        assert after["pending_proposal"] == before["pending_proposal"]
        assert after["conversation_id"] == conversation_id
