"""The installed CLI and real HTTP server enforce Ticket locale creation rules."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
from tests.e2e.harness import JsonObject, ServerHandle

from planner.core.db import connect
from planner.runtime.worker_step_readiness import is_ready_for_worker_step, worker_step_blocker
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import configured_worker_type_registry


def _stored_tickets(server: ServerHandle) -> list[tuple[object, ...]]:
    with sqlite3.connect(server.db_path) as connection:
        return connection.execute("SELECT * FROM tickets ORDER BY id").fetchall()


def test_http_native_provider_creation_allowed_offline_but_both_execution_doors_wait(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
) -> None:
    project = cli(
        server,
        "project",
        "create",
        "--name",
        "Compatible Mac project",
        "--requires-macos",
    )
    request: JsonObject = {
        "title": "Compatible offline Mac request",
        "worker_type": "coding",
        "brief": "The next worker step requires a reconciled Mac connection.",
        "manager": {"kind": "chief", "id": "chief"},
        "employee_backend": "codex",
        "employee_launch_model": "scripted-compatible-model",
        "ceiling": "needs_success_condition",
    }
    for choices in ({"locale": "macos"}, {"project_id": project["id"]}):
        response = httpx.post(server.base + "/api/tickets", json={**request, **choices}, timeout=10)
        assert response.status_code == 200, response.text
        created = response.json()
        assert created["locale"] == "macos" and created["employee_backend"] == "codex"
        assert created["stage"] == "needs_success_condition"
        assert created["pending_proposal"] is None
        assert created["conversation_id"] is None
        with connect(str(server.db_path)) as connection:
            ticket = tickets_data.read_ticket(connection, created["id"])
            definition = configured_worker_type_registry().require(ticket.worker_type)
            assert not is_ready_for_worker_step(
                connection, ticket, worker_type_definition=definition
            )
            blocker = worker_step_blocker(connection, ticket, worker_type_definition=definition)
            assert blocker is not None and "macos" in blocker and "cannot start" in blocker
            assert (
                connection.execute(
                    "SELECT 1 FROM executor_connections WHERE active=1 AND connection_state='ready'"
                ).fetchone()
                is None
            )
            stored_before = tuple(
                connection.execute(
                    "SELECT conversation_id,worker_step_claim FROM tickets WHERE id=?",
                    (ticket.id,),
                ).fetchone()
            )
            assert stored_before == (None, "none")
            conversations_before = connection.execute(
                "SELECT count(*) FROM conversations"
            ).fetchone()[0]
        refused = httpx.post(
            server.base + f"/api/tickets/{created['id']}/conversation/send",
            json={"sender_label": "Owner", "content": [{"piece": "text", "text": "Begin work"}]},
            timeout=10,
        )
        assert refused.status_code == 400, refused.text
        error = refused.json()["error"]
        assert error["code"] == "validation"
        assert error["detail"]["ticket_id"] == created["id"]
        assert error["detail"]["locale"] == "macos"
        assert error["detail"]["machine_locale"] == "vps"
        assert "macos" in error["message"] and "cannot start" in error["message"]
        with connect(str(server.db_path)) as connection:
            assert (
                tuple(
                    connection.execute(
                        "SELECT conversation_id,worker_step_claim FROM tickets WHERE id=?",
                        (created["id"],),
                    ).fetchone()
                )
                == stored_before
            )
            assert (
                connection.execute("SELECT count(*) FROM conversations").fetchone()[0]
                == conversations_before
            )
