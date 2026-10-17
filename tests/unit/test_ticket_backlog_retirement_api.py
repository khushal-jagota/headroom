"""Backlog and Resume remain available; Retire has no HTTP admission."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def test_http_backlog_contact_and_resume(tmp_path: Path) -> None:
    db_path = tmp_path / "isolated.db"
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
        resumed = client.post(
            f"/api/tickets/{tid}/resume", json={"expected_record_sha256": detail["record_sha256"]}
        )
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["is_backlogged"] is False


@pytest.mark.parametrize("stage", ["needs_brief", "cancelled", "done"])
def test_retire_route_is_absent_and_preserves_records(tmp_path: Path, stage: str) -> None:
    db_path = tmp_path / "historical.db"
    conn = connect(str(db_path))
    create_schema(conn)
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
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    try:
        with TestClient(app) as client:
            ticket = client.post(
                "/api/tickets",
                json={
                    "worker_type": "coding",
                    "title": "Preserved record",
                    "kickoff_note": "Agreed brief",
                    "stage": "cancelled",
                    "field_values": {"retirement": "Untrusted creation value"},
                },
            ).json()
            tid = ticket["id"]
            original = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
            assert original["stage"] == "needs_brief"
            values = json.loads(original["field_values"])
            assert "retirement" not in values
            if stage == "cancelled":
                values["retirement"] = json.dumps(
                    {
                        "reason": "Historical disposition",
                        "original_record": original,
                        "original_stage": original["stage"],
                        "original_pending_proposal_json": original["pending_proposal"],
                    }
                )
            # Seed historical terminal records without recreating a cancellation action.
            conn.execute(
                "UPDATE tickets SET stage=?,pending_proposal=?,field_values=? WHERE id=?",
                (
                    stage,
                    original["pending_proposal"] if stage == "needs_brief" else None,
                    json.dumps(values),
                    tid,
                ),
            )
            conn.commit()
            before = dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
            response = client.post(f"/api/tickets/{tid}/retire", json={"reason": "Obsolete"})
            assert response.status_code == 404, response.text
            assert (
                dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()) == before
            )
            detail = client.get("/api/tickets", params={"detail": "full", "id": tid})
            assert detail.status_code == 200, detail.text
            assert detail.json()["stage"] == stage
            assert detail.json()["field_values"] == values
            for patch in ({"stage": "cancelled"}, {"field_values": {"retirement": "Forged"}}):
                rejected = client.patch(f"/api/tickets/{tid}", json=patch)
                assert rejected.status_code == 400, rejected.text
                assert (
                    dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
                    == before
                )
            if stage != "needs_brief":
                assert client.post(f"/api/tickets/{tid}/resume", json={}).status_code != 200
                assert (
                    dict(conn.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone())
                    == before
                )
            if stage == "cancelled":
                historical = client.get(
                    "/api/tickets",
                    params={
                        "detail": "summary",
                        "include_terminal": True,
                        "stage": "cancelled",
                    },
                )
                assert historical.status_code == 200, historical.text
                assert [row["id"] for row in historical.json()["tickets"]] == [tid]
    finally:
        conn.close()
