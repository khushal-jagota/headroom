"""The installed CLI and real HTTP server enforce Ticket locale creation rules."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

import httpx
import pytest
from pydantic import TypeAdapter
from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

from planner.conversation.model_resolution.contracts import (
    CanonicalModelValidation,
    CorrectiveAction,
    SelectionKind,
    ValidationReason,
    ValidationState,
)
from planner.core.db import connect
from planner.runtime.worker_step_readiness import is_ready_for_worker_step, worker_step_blocker
from planner.tickets import data as tickets_data
from planner.worker_types.configuration import configured_worker_type_registry


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


def test_http_unsupported_mac_backend_requirements_explicit_override_and_schedule(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    project = cli(
        server,
        "project",
        "create",
        "--name",
        "Mac project",
        "--requires-macos",
    )
    request: JsonObject = {
        "title": "Mac request",
        "worker_type": "coding",
        "brief": "Check Mac requirements.",
        "manager": {"kind": "chief", "id": "chief"},
        "employee_backend": "claude",
        "employee_launch_model": "scripted-unsupported-model",
    }
    request["employee_backend"] = "unknown-provider"
    for choices in ({"locale": "macos"}, {"project_id": project["id"]}):
        before = _stored_tickets(server)
        refusal = httpx.post(server.base + "/api/tickets", json={**request, **choices}, timeout=10)
        assert refusal.status_code == 400, refusal.text
        assert refusal.json()["error"]["code"] == "validation"
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


@pytest.mark.parametrize("backend", ["codex", "claude", "hermes"])
def test_http_native_provider_creation_allowed_offline_but_both_execution_doors_wait(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    backend: str,
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
        "employee_backend": backend,
        "employee_launch_model": "scripted-compatible-model",
        "ceiling": "needs_success_condition",
    }
    for choices in ({"locale": "macos"}, {"project_id": project["id"]}):
        response = httpx.post(server.base + "/api/tickets", json={**request, **choices}, timeout=10)
        assert response.status_code == 200, response.text
        created = response.json()
        assert created["locale"] == "macos" and created["employee_backend"] == backend
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


@pytest.mark.parametrize("backend", ["codex", "claude", "hermes"])
def test_creation_context_is_readonly_offline_and_write_revalidates(
    server: ServerHandle, cli: Callable[..., JsonObject], backend: str
) -> None:
    from planner.conversation.backend_state import write_model_enablement
    from planner.conversation.contracts import ConversationBackendKey

    project = cli(
        server,
        "project",
        "create",
        "--name",
        "Draft Mac project",
        "--requires-macos",
    )
    draft: JsonObject = {
        "worker_type": "coding",
        "project_id": project["id"],
        "employee_backend": backend,
        "employee_launch_model": "draft-model",
    }
    before = _stored_tickets(server)
    response = httpx.post(server.base + "/api/tickets/creation-context", json=draft, timeout=10)
    assert response.status_code == 200, response.text
    context = response.json()
    validation = TypeAdapter(CanonicalModelValidation).validate_python(context["launch_validation"])
    assert validation.selection.destination.value == "macos"
    assert validation.selection.backend_key.value == backend
    assert validation.selection.submitted_token == "draft-model"
    assert validation.selection.selection_kind is SelectionKind.legacy_unclassified
    assert validation.selection.selection_revision == "draft"
    assert validation.state is ValidationState.unresolved
    assert validation.reason is (
        ValidationReason.catalog_unavailable if backend == "hermes" else ValidationReason.offline
    )
    assert validation.corrective_action is CorrectiveAction.refresh_catalog
    assert validation.evidence is None and validation.unavailable is None
    assert validation.launch_token is None and validation.resolved_model_id is None
    assert context == {
        "resolved_locale": "macos",
        "employee_backend": backend,
        "project_id": project["id"],
        "locale_choices": [
            {"locale": "vps", "compatible": True, "reason": None},
            {"locale": "macos", "compatible": True, "reason": None},
        ],
        "launch_validation": TypeAdapter(CanonicalModelValidation).dump_python(
            validation, mode="json"
        ),
    }
    assert _stored_tickets(server) == before
    health = httpx.get(server.base + "/api/executor/health", timeout=10)
    assert health.status_code == 200, health.text
    assert health.json()["last_contact_at_ms"] is None
    assert health.json()["last_contact_source"] == "vps_received_valid_executor_frame"
    invalid = httpx.post(
        server.base + "/api/tickets/creation-context",
        json={**draft, "locale": None},
        timeout=10,
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "validation"
    assert _stored_tickets(server) == before
    created = httpx.post(
        server.base + "/api/tickets",
        json={**draft, "title": "Accepted offline draft"},
        timeout=10,
    )
    assert created.status_code == 200, created.text
    assert created.json()["locale"] == context["resolved_locale"]
    with connect(str(server.db_path)) as connection:
        write_model_enablement(connection, ConversationBackendKey(backend), "draft-model", False)
        connection.commit()
    before = _stored_tickets(server)
    refused = httpx.post(
        server.base + "/api/tickets",
        json={**draft, "title": "Changed policy"},
        timeout=10,
    )
    assert refused.status_code == 400, refused.text
    assert "disabled" in refused.json()["error"]["message"]
    assert _stored_tickets(server) == before
