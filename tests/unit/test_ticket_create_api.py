"""Ticket creation rejects unsupported keys before any writer or value parser."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

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
from planner.tickets import actions as tickets_actions
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


@pytest.mark.parametrize(
    ("extras", "reported_field"),
    [
        ({"unrecognized": "private arbitrary value"}, "unrecognized"),
        ({"breif": "private misspelled brief"}, "breif"),
        ({"worker_typ": "private misspelled type"}, "worker_typ"),
        ({"stage": "private stage override"}, "stage"),
        ({"field_values": {"retirement": "private forged value"}}, "field_values"),
        (
            {"employee_launch_reasoning_effort": "private effort"},
            "employee_launch_reasoning_effort",
        ),
        ({"kickoff_note": "private legacy brief"}, "kickoff_note"),
        ({"priority": "P0"}, "priority"),
        ({"first_unknown": "private first", "second_unknown": "private second"}, "first_unknown"),
    ],
)
def test_unknown_keys_reject_without_writer_or_database_changes(
    create_api: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
    extras: dict[str, Any],
    reported_field: str,
) -> None:
    client, db_path = create_api
    create = Mock(side_effect=AssertionError("the create writer must not run"))
    monkeypatch.setattr(tickets_actions, "create_ticket", create)
    before = _snapshot(db_path)

    response = client.post(
        "/api/tickets", json={"title": "Rejected intake", "worker_type": "coding", **extras}
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "validation"
    assert error["detail"] == {"field": reported_field}
    assert "private" not in response.text
    create.assert_not_called()
    assert _snapshot(db_path) == before


@pytest.mark.parametrize(
    "invalid_values",
    [
        {},
        {"worker_type": "not-a-worker"},
        {"worker_type": {"invalid": "type"}},
        {"worker_type": "coding", "brief": 42},
        {"worker_type": "coding", "title": ["invalid"]},
        {"worker_type": "coding", "locale": "invalid"},
    ],
)
def test_unknown_guard_precedes_worker_type_and_other_value_parsing(
    create_api: tuple[TestClient, Path], invalid_values: dict[str, Any]
) -> None:
    client, db_path = create_api
    before = _snapshot(db_path)
    response = client.post("/api/tickets", json={**invalid_values, "breif": "private text"})
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "validation"
    assert response.json()["error"]["detail"] == {"field": "breif"}
    assert "private text" not in response.text
    assert _snapshot(db_path) == before


@pytest.mark.parametrize("calendar_key", ["sprint_id", "sprint_item_id", "day", "day_id"])
def test_calendar_key_retains_dedicated_error_before_unknown_key_validation(
    create_api: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
    calendar_key: str,
) -> None:
    client, db_path = create_api
    create = Mock(side_effect=AssertionError("the create writer must not run"))
    monkeypatch.setattr(tickets_actions, "create_ticket", create)
    before = _snapshot(db_path)
    response = client.post(
        "/api/tickets",
        json={"unknown": "private value", calendar_key: None, "worker_type": "invalid"},
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"] == {
        "code": "validation",
        "message": "use orchestrator assignment instead of calendar placement",
        "detail": {},
    }
    create.assert_not_called()
    assert _snapshot(db_path) == before


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


@pytest.mark.parametrize("brief_body", [{}, {"brief": ""}, {"brief": "  \n\t "}])
@pytest.mark.parametrize("ceiling", [None, "needs_implementation"])
def test_omitted_and_blank_briefs_leave_no_proposal_or_field(
    create_api: tuple[TestClient, Path], brief_body: dict[str, str], ceiling: str | None
) -> None:
    client, db_path = create_api
    body: dict[str, Any] = {"title": "Empty Brief", "worker_type": "coding", **brief_body}
    if ceiling is not None:
        body["ceiling"] = ceiling
    response = client.post("/api/tickets", json=body)
    assert response.status_code == 200, response.text
    with connect(str(db_path)) as conn:
        stored = tickets_data.read_ticket(conn, response.json()["id"])
    assert stored.stage == "needs_brief"
    assert stored.field_values == {}
    assert stored.pending_proposal is None


def test_create_accepts_and_preserves_the_supported_optional_keys(
    create_api: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, db_path = create_api
    # This test owns a fictional model, so the real resolver gets scripted evidence.
    # Patch the snapshot boundary after lifespan setup rather than query a local vendor.
    async def hermes_snapshot(
        backend_key: ConversationBackendKey, **kwargs: object,
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
        "orchestrator": {"kind": "chief", "id": "chief"},
        "blocked_by_ticket_ids": [blocker.json()["id"]],
        "ceiling": "needs_implementation",
        "ceiling_holder": {"kind": "chief", "id": "chief"},
        "locale": "vps",
        "employee_backend": "hermes",
        "employee_launch_model": "hermes-model",
    }
    assert set(body) == set(CreateTicketBody.__annotations__)
    response = client.post("/api/tickets", json=body)
    assert response.status_code == 200, response.text
    created = response.json()
    for field in (
        "title", "deadline", "project_id", "orchestrator", "ceiling",
        "ceiling_holder", "locale", "employee_backend", "employee_launch_model",
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


def test_cold_failed_catalogue_does_not_block_creation_or_invent_evidence(
    create_api: tuple[TestClient, Path],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, db_path = create_api
    calls: list[ConversationBackendKey] = []

    async def unavailable_snapshot(
        backend_key: ConversationBackendKey, **kwargs: object,
    ) -> BackendSnapshot:
        calls.append(backend_key)
        raise ValueError("scripted first-run catalogue unavailable")

    monkeypatch.setattr(
        cast(FastAPI, client.app).state.conversation.backend_snapshots,
        "snapshot", unavailable_snapshot,
    )
    requested = tickets_data.requested_ticket_launch_configuration("coding")
    with connect(str(db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM model_catalogue_models").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM model_variant_resolutions").fetchone()[0] == 0
    response = client.post(
        "/api/tickets", json={"title": "Fresh install intake", "worker_type": "coding"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["employee_launch_model"] == requested.employee_launch_model
    assert (
        response.json()["employee_launch_reasoning_effort"]
        == requested.employee_launch_reasoning_effort
    )
    assert calls == [ConversationBackendKey(requested.employee_backend)]
    assert "resolution unavailable" in caplog.text
    with connect(str(db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM model_catalogue_models").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM model_variant_resolutions").fetchone()[0] == 0
