"""Ordinary Ticket PATCH is one atomic edit through the real HTTP boundary."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.model_catalogue import seed_test_model_catalogue
from tests.support.principals import OWNER_PRINCIPAL
from tests.support.probe import seed_probe_worker_type

from planner.conversation.backend_state import BackendStateStore, write_model_enablement
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.model_resolution.data import ModelResolutionStore
from planner.conversation.model_resolution.service import ModelResolutionService
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.runtime import worker_step_readiness
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
)
from planner.worker_types.configuration import load_worker_runtime_definitions


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    # The probe Worker type is stored like any other, so the app reads it from here.
    seed_probe_worker_type(boot)
    load_worker_runtime_definitions(boot)
    seed_test_model_catalogue(boot)
    boot.commit()
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_FAKE_NOW": "2026-07-10T12:00:00+01:00",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path


def _seed_sprint(conn: Connection, sprint_id: str = "sp_edit") -> None:
    conn.execute(
        "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
        "VALUES (?, 'Edit sprint', '2026-07-01', '2026-07-14', 1, 1)",
        (sprint_id,),
    )


def _create_ticket(db_path: Path, **values: Any) -> str:
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type=values.pop("worker_type", "coding"),
            title=values.pop("title", "Before edit"),
            brief=values.pop("brief", "Before note"),
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
            **values,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="brief",
            principal=OWNER_PRINCIPAL,
            now=1,
            next_ceiling=NO_FURTHER,
            next_holder=OWNER_PRINCIPAL,
        )
        return ticket.id
    finally:
        conn.close()


def test_ticket_block_api_validates_endpoints_duplicates_and_active_cycles(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    first = _create_ticket(db_path, title="First")
    second = _create_ticket(db_path, title="Second")
    third = _create_ticket(db_path, title="Third")
    fourth = _create_ticket(db_path, title="Fourth")
    fifth = _create_ticket(db_path, title="Fifth")

    def add(client: TestClient, blocking: str, blocked: str) -> Any:
        return client.put(f"/api/collections/blockers/{blocked}/{blocking}")

    with TestClient(app) as client:
        missing_blocker = add(client, "t_missing", first)
        missing_blocked = add(client, first, "t_missing")
        self_block = add(client, first, first)
        created = add(client, first, second)
        duplicate = add(client, first, second)
        direct_cycle = add(client, second, first)
        assert add(client, third, fourth).status_code == 200
        assert add(client, fourth, fifth).status_code == 200
        longer_cycle = add(client, fifth, third)

    assert missing_blocker.status_code == 400
    assert missing_blocker.json()["error"] == {
        "code": "ticket_block_invalid",
        "message": "blocking_ticket_id must be an existing ticket",
        "detail": {"blocking_ticket_id": "t_missing"},
    }
    assert missing_blocked.status_code == 400
    assert missing_blocked.json()["error"] == {
        "code": "ticket_block_invalid",
        "message": "blocked_ticket_id must be an existing ticket",
        "detail": {"blocked_ticket_id": "t_missing"},
    }
    assert self_block.status_code == 400
    assert self_block.json()["error"]["code"] == "ticket_block_invalid"
    assert created.json() == {
        "collection": "blockers",
        "container_id": second,
        "member_id": first,
        "ok": True,
    }
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "ticket_block_invalid"
    assert direct_cycle.status_code == 400
    assert direct_cycle.json()["error"]["code"] == "ticket_block_cycle"
    assert longer_cycle.status_code == 400
    assert longer_cycle.json()["error"]["code"] == "ticket_block_cycle"


def _create_pristine_ticket(db_path: Path, *, worker_type: str = "probe") -> str:
    conn = connect(str(db_path))
    try:
        return tickets_data.create_ticket(
            conn,
            worker_type=worker_type,
            title="Pristine backend selection",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=200,
        ).id
    finally:
        conn.close()


def _employee_configuration_body(
    employee_backend: str,
    employee_launch_model: str,
    employee_launch_reasoning_effort: str | None = None,
) -> dict[str, object]:
    return {
        "employee_backend": employee_backend,
        "employee_launch_model": employee_launch_model,
        "employee_launch_reasoning_effort": employee_launch_reasoning_effort,
    }


def test_creation_defaults_to_chief_without_calendar_and_preserves_priority_anchors(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    with connect(str(db_path)) as conn:
        _seed_sprint(conn)  # Existing history does not choose live placement.
        conn.execute("UPDATE projects SET priority='P1' WHERE id='project_vylo'")
    with TestClient(app) as client:
        defaulted = client.post("/api/tickets", json={"title": "Default", "worker_type": "coding"})
        project = client.post(
            "/api/tickets",
            json={
                "title": "Project default",
                "worker_type": "coding",
                "project_id": "project_vylo",
                "orchestrator": {"kind": "chief", "id": "chief"},
            },
        )
        explicit = client.post(
            "/api/tickets",
            json={
                "title": "Explicit priority",
                "worker_type": "coding",
                "priority": "P0",
                "project_id": "project_vylo",
                "orchestrator": {"kind": "chief", "id": "chief"},
            },
        )
        for response in (defaulted, project, explicit):
            assert response.status_code == 200, response.text
            assert response.json()["orchestrator"] == {"kind": "chief", "id": "chief"}
            assert response.json()["sprint_id"] is None
            assert response.json()["effective_sprint_id"] is None
            detail = client.get(f"/api/tickets?detail=full&id={response.json()['id']}")
            assert detail.json()["day_ids"] == []
        assert project.json()["project_id"] == "project_vylo"
        assert project.json()["priority"] == "P1"
        assert explicit.json()["priority"] == "P0"
        assert project.json()["resolved_priority_anchors"] == {
            "sprint_item": None,
            "project": {"id": "project_vylo", "name": "Vylo", "priority": "P1"},
        }
        with connect(str(db_path)) as conn:
            before = tuple(conn.iterdump())
        for removed in ({"sprint_id": None}, {"sprint_id": "sp_edit"}, {"sprint_item_id": None}):
            rejected = client.post(
                "/api/tickets",
                json={"title": "Obsolete ingress", "worker_type": "coding", **removed},
            )
            assert rejected.status_code == 400
            assert "orchestrator assignment" in rejected.json()["error"]["message"]
        with connect(str(db_path)) as conn:
            assert tuple(conn.iterdump()) == before
            assert conn.execute("SELECT count(*) FROM days").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM day_tickets").fetchone()[0] == 0


def test_parented_ticket_project_is_derived_and_combined_edits_are_atomic(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        vylo_item = client.post(
            "/api/items", json={"title": "Vylo outcome", "project_id": "project_vylo"}
        ).json()
        personal_item = client.post(
            "/api/items", json={"title": "Personal outcome", "project_id": "project_personal"}
        ).json()
        created = client.post(
            "/api/tickets",
            json={
                "title": "Derived project ticket",
                "worker_type": "coding",
                "orchestrator": {"kind": "sprint_item", "id": vylo_item["id"]},
            },
        )
        assert created.status_code == 200, created.text
        ticket_id = str(created.json()["id"])
        assert created.json()["project_id"] == "project_vylo"
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                is None
            )

        full = client.get("/api/tickets?detail=full&project_id=project_vylo")
        summary = client.get("/api/tickets?detail=summary&project_id=project_vylo&search=Derived")
        assert ticket_id in {row["id"] for row in full.json()["tickets"]}
        assert ticket_id in {row["id"] for row in summary.json()["tickets"]}

        incompatible = client.patch(
            f"/api/tickets/{ticket_id}",
            json={
                "orchestrator": {"kind": "sprint_item", "id": personal_item["id"]},
                "project_id": "project_vylo",
            },
        )
        assert incompatible.status_code == 400
        unchanged = client.get(f"/api/tickets?detail=full&id={ticket_id}").json()
        assert unchanged["sprint_item_id"] == vylo_item["id"]
        assert unchanged["project_id"] == "project_vylo"

        moved = client.patch(
            f"/api/tickets/{ticket_id}",
            json={
                "orchestrator": {"kind": "sprint_item", "id": personal_item["id"]},
                "project_id": "project_personal",
            },
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["project_id"] == "project_personal"
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                is None
            )

        detached = client.patch(
            f"/api/tickets/{ticket_id}", json={"orchestrator": {"kind": "chief", "id": "chief"}}
        )
        assert detached.status_code == 200, detached.text
        assert detached.json()["project_id"] == "project_personal"
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                == "project_personal"
            )

        reattached = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"orchestrator": {"kind": "sprint_item", "id": personal_item["id"]}},
        )
        assert reattached.status_code == 200, reattached.text
        replacement = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"orchestrator": {"kind": "chief", "id": "chief"}, "project_id": "project_vylo"},
        )
        assert replacement.status_code == 200, replacement.text
        assert replacement.json()["project_id"] == "project_vylo"
        with connect(str(db_path)) as conn:
            assert (
                conn.execute("SELECT project_id FROM tickets WHERE id=?", (ticket_id,)).fetchone()[
                    0
                ]
                == "project_vylo"
            )


def test_employee_configuration_endpoint_allows_pristine_statuses(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    awaiting_id = _create_pristine_ticket(db_path)
    empty_id = _create_pristine_ticket(db_path)
    conn = connect(str(db_path))
    # No parked proposal, so this one reads as `empty` rather than awaiting approval.
    conn.execute("UPDATE tickets SET pending_proposal = NULL WHERE id = ?", (empty_id,))
    conn.close()

    with TestClient(app) as client:
        awaiting = client.put(
            f"/api/tickets/{awaiting_id}/employee-configuration",
            json=_employee_configuration_body("claude", "opus"),
        )
        empty = client.put(
            f"/api/tickets/{empty_id}/employee-configuration",
            json=_employee_configuration_body("claude", "opus"),
        )

    assert awaiting.status_code == empty.status_code == 200
    assert awaiting.json()["employee_backend"] == empty.json()["employee_backend"] == "claude"
    assert awaiting.json()["employee_configuration_editable"] is True
    assert empty.json()["employee_configuration_editable"] is True
    check = connect(str(db_path))
    try:
        for ticket_id in (awaiting_id, empty_id):
            stored = tickets_data.read_ticket(check, ticket_id)
            assert stored.employee_backend == "claude"
            # The new backend's model came with it, so the Ticket says what it runs on.
            assert stored.employee_launch_model == "opus"
            assert stored.employee_launch_reasoning_effort is None
    finally:
        check.close()


def test_employee_configuration_rejects_a_disabled_model_without_a_partial_write(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)
    conn = connect(str(db_path))
    before = tickets_data.employee_launch_configuration(tickets_data.read_ticket(conn, ticket_id))
    write_model_enablement(conn, ConversationBackendKey.claude, "disabled-model", False)
    conn.close()

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "disabled-model"),
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == (
        'Model "disabled-model" is disabled. Choose an enabled model.'
    )
    check = connect(str(db_path))
    try:
        assert (
            tickets_data.employee_launch_configuration(tickets_data.read_ticket(check, ticket_id))
            == before
        )
    finally:
        check.close()


def _backend_snapshot(
    backend_key: str, models: tuple[BackendModel, ...], efforts: tuple[str, ...]
) -> BackendSnapshot:
    return BackendSnapshot(
        backend_key=ConversationBackendKey(backend_key),
        installed=True,
        executable_path=f"/probe/{backend_key}",
        version="1.0.0",
        identity=None,
        available_models=models,
        reasoning_effort_options=efforts,
        default_model_id=models[0].model_id if models else None,
        default_reasoning_effort=efforts[0] if efforts else None,
        update_advisory=None,
        diagnoses=(),
    )


def test_employee_configuration_preserves_effort_and_validates_each_backend(
    tmp_path: Path,
) -> None:
    class BackendSnapshots:
        async def snapshot(self, backend_key: str, **kwargs: object) -> BackendSnapshot:
            if backend_key == "claude":
                return _backend_snapshot(
                    backend_key,
                    (BackendModel("opus", reasoning_effort_options=("medium",)),),
                    ("medium",),
                )
            return _backend_snapshot(
                backend_key,
                (
                    BackendModel(
                        model_id="openai-codex:gpt-5.6-sol",
                        reasoning_effort_options=("low", "high"),
                    ),
                    BackendModel(
                        model_id="openai-codex:gpt-5.5",
                        reasoning_effort_options=("low",),
                    ),
                ),
                ("low", "high"),
            )

    app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)

    with TestClient(app) as client:
        # The lifespan builds the real backend snapshot service on the way up, so the
        # stand-in goes on once the app is running rather than before it starts.
        snapshots = BackendSnapshots()
        app.state.conversation = SimpleNamespace(
            backend_snapshots=snapshots,
            model_resolution=ModelResolutionService(
                snapshots=snapshots,
                store=ModelResolutionStore(str(db_path)),
                backend_state=BackendStateStore(str(db_path)),
            ),
        )
        selected = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes", "openai-codex:gpt-5.6-sol", "high"),
        )
        model_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes", "openai-codex:gpt-5.5", "high"),
        )
        backend_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "opus", "medium"),
        )
        switched_back = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes", "openai-codex:gpt-5.6-sol", "high"),
        )

    assert selected.status_code == 200
    assert (
        selected.json()["employee_launch_model"],
        selected.json()["employee_launch_reasoning_effort"],
    ) == ("openai-codex:gpt-5.6-sol", "high")
    assert model_changed.status_code == 400
    assert "reasoning effort" in model_changed.json()["error"]["message"]
    # An explicit effort survives a model change, or the request refuses.
    assert backend_changed.status_code == 200
    assert (
        backend_changed.json()["employee_backend"],
        backend_changed.json()["employee_launch_model"],
        backend_changed.json()["employee_launch_reasoning_effort"],
    ) == ("claude", "opus", "medium")
    assert switched_back.status_code == 200
    assert (
        switched_back.json()["employee_backend"],
        switched_back.json()["employee_launch_model"],
        switched_back.json()["employee_launch_reasoning_effort"],
    ) == ("hermes", "openai-codex:gpt-5.6-sol", "high")


def test_an_active_worker_does_not_block_an_ordinary_edit(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    conn = connect(str(db_path))
    try:
        started = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            readiness_check=worker_step_readiness.is_ready_for_worker_step,
            now=2,
        )
        assert started is not None
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"priority": "P1", "title": "Edited during active work"},
        )

    assert response.status_code == 200, response.json()
    assert response.json()["ticket_status"] == "agent"
    assert response.json()["title"] == "Edited during active work"
    assert response.json()["priority"] == "P1"
