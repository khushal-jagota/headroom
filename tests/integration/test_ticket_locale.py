"""The installed CLI and real HTTP server enforce Ticket locale creation rules."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle


def _stored_tickets(server: ServerHandle) -> list[tuple[object, ...]]:
    with sqlite3.connect(server.db_path) as connection:
        return connection.execute("SELECT * FROM tickets ORDER BY id").fetchall()


def test_cli_default_locale_read_and_idle_same_locale_move(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    created = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Locale process proof",
        "--worker-type",
        "coding",
        "--kickoff-note",
        "Check machine selection.",
    )
    assert created["locale"] == "vps"
    shown = cli(server, "ticket", "show", created["id"])
    assert shown["header"]["locale"] == "vps"
    before = _stored_tickets(server)
    moved = cli(server, "ticket", "move", created["id"], "--locale", "vps")
    assert moved["locale"] == "vps"
    assert _stored_tickets(server) == before
    detail = api.get(server, f"/api/tickets?detail=full&id={created['id']}")
    assert detail["locale"] == "vps"


def test_http_mac_refusal_requirements_explicit_override_and_schedule(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    project = cli(
        server,
        "project",
        "create",
        "--name",
        "Mac project",
        "--priority",
        "P2",
        "--requires-macos",
    )
    request: JsonObject = {
        "title": "Mac request",
        "worker_type": "coding",
        "brief": "Check Mac requirements.",
        "orchestrator": {"kind": "chief", "id": "chief"},
    }
    for choices in ({"locale": "macos"}, {"project_id": project["id"]}):
        before = _stored_tickets(server)
        refusal = httpx.post(server.base + "/api/tickets", json={**request, **choices}, timeout=10)
        assert refusal.status_code == 400, refusal.text
        error = refusal.json()["error"]
        assert error["detail"]["locale"] == "macos"
        assert error["detail"]["employee_backend"]
        assert error["detail"]["reason"]
        assert "macos" in error["message"]
        assert _stored_tickets(server) == before

    explicit = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Explicit VPS override",
        "--worker-type",
        "coding",
        "--project-id",
        project["id"],
        "--locale",
        "vps",
        "--kickoff-note",
        "The explicit locale wins.",
    )
    assert explicit["locale"] == "vps"
    assert explicit["project_id"] == project["id"]
    schedule = cli(
        server,
        "schedule",
        "create",
        "--title",
        "Explicit VPS schedule",
        "--worker-type",
        "coding",
        "--time",
        "23:59",
        "--project-id",
        project["id"],
        "--locale",
        "vps",
        "--kickoff-note",
        "Keep the resolved locale.",
    )
    assert schedule["locale"] == "vps"
    assert api.get(server, f"/api/schedules/{schedule['id']}")["locale"] == "vps"
    edited = cli(server, "schedule", "set", schedule["id"], "title", "--value", "Edited title")
    assert edited["locale"] == "vps"
