"""Ordinary Ticket PATCH is one atomic edit through the real HTTP boundary."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import Priority
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.runtime import worker_step_readiness
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
)
from planner.worker_context import data as worker_context_data


def _make_app(
    tmp_path: Path, *, trace: list[str] | None = None
) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
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
        conn = connect(str(db_path))
        if trace is not None:
            conn.set_trace_callback(trace.append)
        return conn

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
            kickoff_note=values.pop("kickoff_note", "Before note"),
            actor="unattributed",
            now=1,
            title_max_chars=200,
            **values,
        )
        ticket = tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="unattributed",
            now=1,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        return ticket.id
    finally:
        conn.close()


def _create_pristine_ticket(db_path: Path, *, worker_type: str = "probe") -> str:
    conn = connect(str(db_path))
    try:
        return tickets_data.create_ticket(
            conn,
            worker_type=worker_type,
            title="Pristine backend selection",
            actor="unattributed",
            now=1,
            title_max_chars=200,
        ).id
    finally:
        conn.close()


@pytest.fixture
def probe_runtime() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


def _snapshot(db_path: Path, ticket_id: str) -> dict[str, Any]:
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        return {
            "values": (
                ticket.title,
                ticket.priority.value,
                ticket.deadline,
                ticket.project_id,
                ticket.effective_sprint_id,
                ticket.employee_backend,
                ticket.employee_launch_model,
                ticket.employee_launch_reasoning_effort,
                str(ticket.stage),
                ticket.ticket_status.value,
            ),
            "updated_at": ticket.updated_at,
            "context": tuple(
                (item.context_key, item.text, item.revision)
                for item in worker_context_data.snapshot(conn, ticket_id).items
            ),
        }
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


def test_ticket_creation_defaults_to_today_and_current_sprint_but_preserves_explicit_backlog(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        _seed_sprint(conn)
        conn.execute(
            "UPDATE projects SET priority = 'P1' WHERE id = 'project_vylo'"
        )
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        defaulted = client.post(
            "/api/tickets", json={"title": "Default placement", "worker_type": "coding"}
        )
        reused = client.post(
            "/api/tickets", json={"title": "Same fallback", "worker_type": "coding"}
        )
        project_fallback = client.post(
            "/api/tickets",
            json={
                "title": "Project fallback",
                "worker_type": "coding",
                "project_id": "project_vylo",
            },
        )
        explicit_backlog = client.post(
            "/api/tickets",
            json={
                "title": "Explicit backlog",
                "worker_type": "coding",
                "sprint_item_id": None,
            },
        )
        project_priority_default = client.post(
            "/api/tickets",
            json={
                "title": "Assessed project default",
                "worker_type": "coding",
                "project_id": "project_vylo",
                "sprint_item_id": None,
            },
        )
        explicit_priority = client.post(
            "/api/tickets",
            json={
                "title": "Explicit priority",
                "worker_type": "coding",
                "priority": "P0",
                "project_id": "project_vylo",
                "sprint_item_id": None,
            },
        )

    assert (
        defaulted.status_code
        == reused.status_code
        == project_fallback.status_code
        == explicit_backlog.status_code
        == project_priority_default.status_code
        == explicit_priority.status_code
        == 200
    )
    assert defaulted.json()["effective_sprint_id"] == "sp_edit"
    assert defaulted.json()["sprint_id"] == "sp_edit"
    assert defaulted.json()["sprint_item_id"] is None
    assert reused.json()["sprint_item_id"] is None
    assert project_fallback.json()["project_id"] == "project_vylo"
    assert project_fallback.json()["effective_sprint_id"] == "sp_edit"
    assert explicit_backlog.json()["effective_sprint_id"] is None
    assert project_priority_default.json()["priority"] == "P1"
    assert explicit_priority.json()["priority"] == "P0"
    assert project_priority_default.json()["resolved_priority_anchors"] == {
        "sprint_item": None,
        "project": {
            "id": "project_vylo",
            "name": "Vylo",
            "priority": "P1",
        },
    }

    conn = connect(str(db_path))
    try:
        assert conn.execute(
            "SELECT count(*) FROM sprint_items WHERE kind = 'other'"
        ).fetchone()[0] == 0
    finally:
        conn.close()
    with TestClient(app) as client:
        assert client.get(f"/api/tickets/{defaulted.json()['id']}").json()[
            "day_ids"
        ] == ["day_2026-07-10"]
        assert client.get(f"/api/tickets/{explicit_backlog.json()['id']}").json()[
            "day_ids"
        ] == ["day_2026-07-10"]


def test_failed_creation_does_not_create_an_other_item(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        _seed_sprint(conn)
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            "/api/tickets",
            json={
                "title": "Must not land",
                "worker_type": "coding",
                "blocked_by_ticket_ids": ["t_missing"],
            },
        )

    assert response.status_code == 400
    conn = connect(str(db_path))
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM sprint_items WHERE kind = 'other'"
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_planning_ticket_creation_uses_the_sprints_planning_item(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        _seed_sprint(conn)
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        defaulted = client.post(
            "/api/tickets", json={"title": "Plan today", "worker_type": "planning-day"}
        )
        explicit = client.post(
            "/api/tickets",
            json={
                "title": "Plan explicit Sprint",
                "worker_type": "planning-sprint",
                "sprint_id": "sp_edit",
            },
        )
        initiative = client.post(
            "/api/tickets",
            json={"title": "Plan initiative", "worker_type": "initiative_planning"},
        )

    for response in (defaulted, explicit):
        assert response.status_code == 200
        assert response.json()["project_id"] == "project_personal"
        assert response.json()["sprint_id"] == "sp_edit"
        assert response.json()["sprint_item_id"] == "si_planning_edit"
    assert initiative.status_code == 200
    assert initiative.json()["project_id"] == "project_other"
    assert initiative.json()["sprint_item_id"] is None

def test_employee_configuration_endpoint_allows_pristine_statuses(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    awaiting_id = _create_pristine_ticket(db_path)
    empty_id = _create_pristine_ticket(db_path)
    conn = connect(str(db_path))
    conn.execute("UPDATE tickets SET ticket_status = 'empty' WHERE id = ?", (empty_id,))
    conn.close()

    with TestClient(app) as client:
        awaiting = client.put(
            f"/api/tickets/{awaiting_id}/employee-configuration",
            json=_employee_configuration_body("claude", "claude-model"),
        )
        empty = client.put(
            f"/api/tickets/{empty_id}/employee-configuration",
            json=_employee_configuration_body("claude", "claude-model"),
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
            assert stored.employee_launch_model == "claude-model"
            assert stored.employee_launch_reasoning_effort is None
    finally:
        check.close()


def test_employee_configuration_rejects_a_disabled_model_without_a_partial_write(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)
    conn = connect(str(db_path))
    before = tickets_data.employee_launch_configuration(
        tickets_data.read_ticket(conn, ticket_id)
    )
    write_model_enablement(conn, ConversationBackendKey.claude, "disabled-model", False)
    conn.close()

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "disabled-model"),
        )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Employee model is disabled"
    check = connect(str(db_path))
    try:
        assert tickets_data.employee_launch_configuration(
            tickets_data.read_ticket(check, ticket_id)
        ) == before
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


def test_employee_configuration_writer_normalizes_worker_and_model_dependencies(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    class BackendSnapshots:
        async def snapshot(
            self, backend_key: str, *, refresh: bool = False
        ) -> BackendSnapshot:
            del refresh
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
        app.state.conversation = SimpleNamespace(backend_snapshots=BackendSnapshots())
        selected = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body(
                "hermes", "openai-codex:gpt-5.6-sol", "high"
            ),
        )
        model_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body(
                "hermes", "openai-codex:gpt-5.5", "high"
            ),
        )
        backend_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "claude-model", "medium"),
        )
        switched_back = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body(
                "hermes", "openai-codex:gpt-5.6-sol", "high"
            ),
        )

    assert selected.status_code == 200
    assert (
        selected.json()["employee_launch_model"],
        selected.json()["employee_launch_reasoning_effort"],
    ) == ("openai-codex:gpt-5.6-sol", "high")
    assert model_changed.status_code == 200
    # probe-b takes only "low", and the model changed in the same write, so the effort
    # that no longer applies falls back to the new model's own rather than being refused.
    assert (
        model_changed.json()["employee_launch_model"],
        model_changed.json()["employee_launch_reasoning_effort"],
    ) == ("openai-codex:gpt-5.5", None)
    # A backend change is taken whole. Nothing carries over from the backend being left —
    # a model id belongs to the backend that named it — so the body is the whole answer,
    # and there is no catalog of the new backend's to weigh it against here.
    assert backend_changed.status_code == 200
    assert (
        backend_changed.json()["employee_backend"],
        backend_changed.json()["employee_launch_model"],
        backend_changed.json()["employee_launch_reasoning_effort"],
    ) == ("claude", "claude-model", "medium")
    assert switched_back.status_code == 200
    assert (
        switched_back.json()["employee_backend"],
        switched_back.json()["employee_launch_model"],
        switched_back.json()["employee_launch_reasoning_effort"],
    ) == ("hermes", "openai-codex:gpt-5.6-sol", "high")


def test_compound_patch_changes_all_fields_in_canonical_order_with_one_context_signal(
    tmp_path: Path,
) -> None:
    trace: list[str] = []
    app, db_path = _make_app(tmp_path, trace=trace)
    conn = connect(str(db_path))
    try:
        _seed_sprint(conn)
    finally:
        conn.close()
    ticket_id = _create_ticket(db_path)
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        # The trace below proves the PATCH transaction. Startup owns its own fail-fast
        # skill reconciliation transactions, which finish before this request begins.
        trace.clear()
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={
                "project": "Vylo",
                "deadline": "2026-08-01",
                "priority": "P1",
                "title": "After edit",
            },
        )

    assert response.status_code == 200, response.json()
    assert response.json()["title"] == "After edit"
    assert response.json()["priority"] == "P1"
    assert response.json()["deadline"] == "2026-08-01"
    assert response.json()["project_id"] == "project_vylo"
    assert response.json()["effective_sprint_id"] is None
    assert before["values"] != _snapshot(db_path, ticket_id)["values"]
    assert _snapshot(db_path, ticket_id)["context"] == (
        (
            "ticket_changed",
            "This ticket changed outside your worker turn. Reread the ticket before continuing.",
            1,
        ),
    )
    transaction_statements = [statement.strip() for statement in trace]
    assert (
        sum(statement == "BEGIN IMMEDIATE" for statement in transaction_statements) == 1
    )
    ticket_updates = [
        statement
        for statement in transaction_statements
        if statement.upper().startswith("UPDATE TICKETS SET")
    ]
    assert len(ticket_updates) == 1
    begin_index = transaction_statements.index("BEGIN IMMEDIATE")
    statements_under_lock = transaction_statements[begin_index:]
    assert any(
        "SELECT 1 FROM PROJECTS WHERE ID" in statement.upper()
        for statement in statements_under_lock
    )


def test_compound_patch_rolls_back_the_row_and_context_when_a_later_write_fails(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    conn = connect(str(db_path))
    try:
        # The worker-context notice is written after the ticket row, inside the same
        # transaction, so failing it proves the row write rolls back with it.
        conn.execute(
            "CREATE TRIGGER abort_worker_context_notice "
            "BEFORE INSERT ON pending_worker_context "
            "BEGIN SELECT RAISE(ABORT, 'forced worker context failure'); END"
        )
    finally:
        conn.close()
    before = _snapshot(db_path, ticket_id)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"title": "Must roll back", "priority": "P1"},
        )

    assert response.status_code == 500
    assert _snapshot(db_path, ticket_id) == before


def test_patch_of_existing_non_null_values_is_a_true_noop(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        _seed_sprint(conn)
    finally:
        conn.close()
    ticket_id = _create_ticket(
        db_path,
        priority=Priority.P1,
        deadline="2026-08-01",
        project_id="project_vylo",
    )
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={
                "title": "Before edit",
                "priority": "P1",
                "deadline": "2026-08-01",
                "project": "vylo",
                "project_id": "project_vylo",
            },
        )

    assert response.status_code == 200, response.json()
    assert _snapshot(db_path, ticket_id) == before


def test_project_selectors_keep_their_existing_success_contract(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        name_id = _create_ticket(db_path)
        name = client.patch(f"/api/tickets/{name_id}", json={"project": "vYlO"})
        assert name.status_code == 200, name.json()
        assert name.json()["project_id"] == "project_vylo"

        id_id = _create_ticket(db_path)
        by_id = client.patch(
            f"/api/tickets/{id_id}", json={"project_id": "project_vylo"}
        )
        assert by_id.status_code == 200, by_id.json()
        assert by_id.json()["project"] == "Vylo"

        matching_id = _create_ticket(db_path)
        matching = client.patch(
            f"/api/tickets/{matching_id}",
            json={"project": "Vylo", "project_id": "project_vylo"},
        )
        assert matching.status_code == 200, matching.json()
        assert matching.json()["project_id"] == "project_vylo"

        cleared = client.patch(
            f"/api/tickets/{matching_id}",
            json={"project": None, "project_id": None},
        )
        assert cleared.status_code == 200, cleared.json()
        assert cleared.json()["project_id"] is None


def test_an_active_worker_does_not_block_an_ordinary_edit(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    conn = connect(str(db_path))
    try:
        planning_day_id = "day_2026-07-10"
        days_data.add_day_ticket(conn, planning_day_id, ticket_id, 2)
        started = tickets_data.claim_ticket_for_worker_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: planning_day_id,
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
    assert _snapshot(db_path, ticket_id)["context"][-1][2] == 1
