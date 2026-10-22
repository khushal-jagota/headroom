"""Ticket creation rejects unsupported keys before any writer or value parser."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import data as tickets_data
from planner.tickets.contracts import CreateTicketBody


@pytest.fixture
def create_api(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    db_path = tmp_path / "ticket-create.db"
    with closing(connect(str(db_path))) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_FAKE_NOW": "2026-10-17T12:00:00+02:00",
        },
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:
        yield client, db_path


def _snapshot(db_path: Path) -> tuple[str, ...]:
    # This includes Ticket fields, proposals, conversations, and their event records.
    with connect(str(db_path)) as conn:
        return tuple(conn.iterdump())


@pytest.mark.parametrize("ceiling", [None, "needs_implementation"])
def test_brief_persists_as_a_proposal_or_settled_field_for_the_creation_ceiling(
    create_api: tuple[TestClient, Path], ceiling: str | None
) -> None:
    client, db_path = create_api
    brief = "  Preserve this exact Brief.\n\nInclude the second paragraph.  "
    body: dict[str, Any] = {"title": "Real Brief", "worker_type": "coding", "brief": brief}
    if ceiling is not None:
        body["ceiling"] = ceiling
    response = client.post("/api/tickets", json=body)
    assert response.status_code == 200, response.text
    created = response.json()
    with connect(str(db_path)) as conn:
        stored = tickets_data.read_ticket(conn, created["id"])
    if ceiling is None:
        assert created["stage"] == stored.stage == "needs_brief"
        assert stored.field_values.get("brief") is None
        assert stored.pending_proposal is not None
        assert stored.pending_proposal.field == "brief"
        assert stored.pending_proposal.body == brief
        assert created["pending_proposal"]["body"] == brief
    else:
        assert created["stage"] == stored.stage == "needs_success_condition"
        assert stored.ceiling == ceiling
        assert stored.field_values["brief"] == brief
        assert stored.pending_proposal is None
        assert created["field_values"]["brief"] == brief


def test_create_accepts_and_preserves_the_supported_optional_keys(
    create_api: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = create_api

    # This test owns a fictional model, so the real resolver gets scripted evidence.
    # Patch the snapshot boundary after lifespan setup rather than query a local vendor.
    async def hermes_snapshot(
        backend_key: ConversationBackendKey,
        **kwargs: object,
    ) -> BackendSnapshot:
        assert backend_key is ConversationBackendKey.hermes
        return BackendSnapshot(
            backend_key=backend_key,
            installed=True,
            executable_path="/scripted/hermes",
            version="1.0.0",
            identity=None,
            available_models=(BackendModel("hermes-model"),),
            reasoning_effort_options=(),
            default_model_id="hermes-model",
            default_reasoning_effort=None,
            update_advisory=None,
            diagnoses=(),
        )

    monkeypatch.setattr(
        cast(FastAPI, client.app).state.conversation.backend_snapshots,
        "snapshot",
        hermes_snapshot,
    )
    blocker = client.post("/api/tickets", json={"worker_type": "coding", "title": "Blocker"})
    assert blocker.status_code == 200, blocker.text
    body = {
        "worker_type": "coding",
        "title": "All supported create keys",
        "brief": "Settle this Brief at creation.",
        "deadline": "2026-12-31",
        "project": "Vylo",
        "project_id": "project_vylo",
        "manager": {"kind": "chief", "id": "chief"},
        "blocked_by_ticket_ids": [blocker.json()["id"]],
        "ceiling": "needs_implementation",
        "ceiling_holder": {"kind": "chief", "id": "chief"},
        "locale": "vps",
        "employee_backend": "hermes",
        "employee_launch_model": "hermes-model",
        "employee_launch_reasoning_effort": None,
    }
    assert set(body) == set(CreateTicketBody.__annotations__)
    response = client.post("/api/tickets", json=body)
    assert response.status_code == 200, response.text
    created = response.json()
    for field in (
        "title",
        "deadline",
        "project_id",
        "manager",
        "ceiling",
        "ceiling_holder",
        "locale",
        "employee_backend",
        "employee_launch_model",
    ):
        assert created[field] == body[field]
    assert created["field_values"]["brief"] == body["brief"]
    assert created["pending_proposal"] is None
    detail = client.get(f"/api/tickets?detail=full&id={created['id']}").json()
    assert detail["blocked"] is True
    assert [row["ticket_id"] for row in detail["blocker_summary"]["blocked_by"]] == [
        blocker.json()["id"]
    ]
    with connect(str(db_path)) as conn:
        stored = tickets_data.read_ticket(conn, created["id"])
    assert stored.field_values["brief"] == body["brief"]


@pytest.fixture
def effort_catalogue(create_api: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = create_api
    own = tickets_data.requested_ticket_launch_configuration("coding")

    async def snapshot(backend_key: ConversationBackendKey, **kwargs: object) -> BackendSnapshot:
        return BackendSnapshot(
            backend_key=backend_key,
            installed=True,
            executable_path="/scripted/backend",
            version="1",
            identity=None,
            available_models=(
                BackendModel(
                    "opus"
                    if backend_key is ConversationBackendKey.claude
                    else str(own.employee_launch_model),
                    reasoning_effort_options=("low", "medium", "high"),
                ),
            ),
            # A backend-wide option must never authorize a per-model effort.
            reasoning_effort_options=("low", "medium", "high", "ultra"),
            default_model_id=None,
            default_reasoning_effort=None,
            update_advisory=None,
            diagnoses=(),
        )

    monkeypatch.setattr(
        cast(FastAPI, client.app).state.conversation.backend_snapshots,
        "snapshot",
        snapshot,
    )


def test_issued_assignment_inputs_emit_canonical_managers_and_refuse_conflicts(
    create_api: tuple[TestClient, Path],
) -> None:
    client, db_path = create_api
    project = client.post("/api/projects", json={"name": "Compatibility"}).json()
    manager = client.post(
        "/api/managers", json={"title": "Retained Manager", "project_id": project["id"]}
    ).json()
    assignment = {"kind": "manager", "id": manager["id"]}
    issued = {"kind": "sprint_item", "id": manager["id"]}
    created = client.post(
        "/api/tickets",
        json={
            "title": "Issued input",
            "worker_type": "coding",
            "orchestrator": issued,
            "ceiling_holder": assignment,
        },
    )
    assert created.status_code == 200, created.text
    ticket = created.json()
    assert ticket["manager"] == assignment
    assert ticket["manager_id"] == manager["id"]
    assert ticket["ceiling_holder"] == assignment
    assert not {"orchestrator", "sprint_item_id", "orchestrator_kind"} & ticket.keys()
    listed = client.get(
        "/api/tickets",
        params={
            "detail": "summary",
            "orchestrator_kind": "sprint_item",
            "orchestrator_id": manager["id"],
        },
    ).json()
    assert [row["id"] for row in listed["tickets"]] == [ticket["id"]]
    compatible = client.patch(
        f"/api/tickets/{ticket['id']}", json={"manager": assignment, "orchestrator": issued}
    )
    assert compatible.status_code == 200, compatible.text
    before = _snapshot(db_path)
    conflict = {"manager": assignment, "orchestrator": {"kind": "chief", "id": "chief"}}
    for endpoint, body in (
        ("/api/tickets", {**conflict, "title": "Conflict", "worker_type": "coding"}),
        (
            "/api/schedules",
            {**conflict, "title": "Conflict", "worker_type": "coding", "local_time": "09:00"},
        ),
    ):
        response = client.post(endpoint, json=body)
        assert response.status_code == 400, response.text
    response = client.patch(f"/api/tickets/{ticket['id']}", json=conflict)
    assert response.status_code == 400, response.text
    assert _snapshot(db_path) == before


def test_deploy_interruption_summary_uses_request_connection_on_its_own_thread(
    create_api: tuple[TestClient, Path],
) -> None:
    client, db_path = create_api
    with closing(connect(str(db_path))) as conn:
        conn.execute(
            "INSERT INTO deploy_interruption_accounts "
            "(capture_id,deployment_id,captured_at,capture_state,detail) "
            "VALUES ('capture','deployment',1,'unavailable','Capture timed out.')"
        )
        conn.commit()
    response = client.get("/api/deployment-interruptions")
    assert response.status_code == 200
    assert response.json() == {
        "accounts": [
            {
                "deployment_id": "deployment",
                "capture_state": "unavailable",
                "detail": "Capture timed out.",
                "entries": [],
            }
        ]
    }


def test_create_refuses_holders_without_authority_before_kickoff_routing(
    create_api: tuple[TestClient, Path],
) -> None:
    client, db_path = create_api
    manager = client.post(
        "/api/managers", json={"title": "Unrelated Manager", "project_id": "project_vylo"}
    )
    reviewer = client.post(
        "/api/tickets", json={"title": "Ordinary Ticket", "worker_type": "coding"}
    )
    assert manager.status_code == reviewer.status_code == 200
    for holder in (
        {"kind": "manager", "id": manager.json()["id"]},
        {"kind": "ticket", "id": reviewer.json()["id"]},
    ):
        before = _snapshot(db_path)
        refused = client.post(
            "/api/tickets",
            json={
                "title": "Invalid reviewer", "worker_type": "coding",
                "brief": "This Brief must not normalize the invalid holder to owner.",
                "ceiling_holder": holder,
            },
        )
        assert refused.status_code == 400, refused.text
        error = refused.json()["error"]
        assert error["code"] == "validation"
        assert error["message"] == "ceiling holder must have authority to decide this Ticket"
        assert error["detail"]["holder"] == holder
        assert error["detail"]["ticket_id"]
        assert _snapshot(db_path) == before
