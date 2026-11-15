"""The installed CLI and real HTTP server enforce Ticket locale creation rules.

A Mac Ticket is created with no Mac at all. Neither execution door starts it until a
compatible Mac process has leased: the owner's send is refused, and readiness waits.
Once a process is known but away, readiness says it is waiting for the Mac.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
from tests.e2e.harness import JsonObject, ServerHandle
from tests.support.executor_presence import mac_identity, make_current

from planner.core.db import connect
from planner.executor.clock import now_ms
from planner.runtime.worker_step_readiness import is_ready_for_worker_step, worker_step_blocker
from planner.tickets import data as tickets_data
from planner.tickets.locale_configuration import WAITING_FOR_THE_MAC


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
    )
    request: JsonObject = {
        "title": "Compatible offline Mac request",
        "worker_type": "coding",
        "brief": "The next worker step requires a leased Mac executor.",
        "manager": {"kind": "chief", "id": "chief"},
        "employee_backend": "codex",
        "employee_launch_model": "scripted-compatible-model",
        "ceiling": "needs_what_changes",
    }
    for choices in ({"locale": "macos", "project_id": project["id"]},):
        response = httpx.post(server.base + "/api/tickets", json={**request, **choices}, timeout=10)
        assert response.status_code == 200, response.text
        created = response.json()
        assert created["locale"] == "macos" and created["employee_backend"] == "codex"
        assert created["stage"] == "needs_what_changes"
        assert created["pending_proposal"] is None
        assert created["conversation_id"] is None
        with connect(str(server.db_path)) as connection:
            ticket = tickets_data.read_ticket(connection, created["id"])
            definition = ticket.workflow
            assert not is_ready_for_worker_step(
                connection, ticket, worker_type_definition=definition
            )
            blocker = worker_step_blocker(connection, ticket, worker_type_definition=definition)
            assert blocker is not None and "Mac Tickets cannot start" in blocker
            assert connection.execute("SELECT 1 FROM mac_executor_presence").fetchone() is None
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
        assert "Mac Tickets cannot start" in error["message"]
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
        # A compatible process leased once, two minutes ago: known, but away. Readiness
        # waits for it rather than putting a start in the mailbox.
        make_current(str(server.db_path), mac_identity(), at_ms=now_ms() - 120_000)
        with connect(str(server.db_path)) as connection:
            ticket = tickets_data.read_ticket(connection, created["id"])
            assert not is_ready_for_worker_step(
                connection, ticket, worker_type_definition=ticket.workflow
            )
            assert (
                worker_step_blocker(connection, ticket, worker_type_definition=ticket.workflow)
                == WAITING_FOR_THE_MAC
            )
