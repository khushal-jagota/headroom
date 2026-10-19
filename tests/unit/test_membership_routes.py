"""One way to put a thing in a collection, and one way to take it out.

Every collection keeps the membership rules and refusals it had when each one had
its own pair of routes. These tests state those rules against the single entry point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)})
    return (
        create_app(
            config,
            build_clock(config),
            lambda: connect(str(db_path)),
            conversation_system_for_test=InMemoryConversationSystem(),
        ),
        db_path,
    )


def _member(client: TestClient, collection: str, container: str, member: str) -> str:
    return f"/api/collections/{collection}/{container}/{member}"


def _item(client: TestClient, title: str = "An outcome") -> dict[str, Any]:
    created = client.post("/api/items", json={"title": title, "project_id": "project_vylo"})
    assert created.status_code == 200, created.text
    return cast(dict[str, Any], created.json())


def _ticket(
    client: TestClient,
    title: str = "A ticket",
    item_id: str | None = None,
    worker_type: str = "coding",
) -> str:
    body: dict[str, Any] = {
        "worker_type": worker_type,
        "title": title,
        "brief": "Start here.",
    }
    if item_id is not None:
        body["orchestrator"] = {"kind": "sprint_item", "id": item_id}
    created = client.post("/api/tickets", json=body)
    assert created.status_code == 200, created.text
    return str(created.json()["id"])


def _supervisor_headers(item_id: str) -> dict[str, str]:
    return {"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": item_id}


def _worker_headers(ticket_id: str) -> dict[str, str]:
    return {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id}


# --- each collection, added and removed ---------------------------------------






# --- the refusals each collection keeps ---------------------------------------








# --- the same call, with a supervisor's authority -----------------------------
