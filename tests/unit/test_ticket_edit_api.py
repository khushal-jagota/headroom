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
    TicketStatus,
)
from planner.worker_context import data as worker_context_data


def _make_app(tmp_path: Path, *, trace: list[str] | None = None) -> tuple[FastAPI, Path]:
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
                ticket.sprint_id,
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
    employee_launch_model: str | None = None,
    employee_launch_reasoning_effort: str | None = None,
) -> dict[str, object]:
    return {
        "employee_backend": employee_backend,
        "employee_launch_model": employee_launch_model,
        "employee_launch_reasoning_effort": employee_launch_reasoning_effort,
    }


def test_ticket_creation_copies_worker_type_configuration_once(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        defaulted = client.post(
            "/api/tickets", json={"title": "Default backend", "worker_type": "probe"}
        )
        overridden = client.post(
            "/api/tickets",
            json={
                "title": "Override backend",
                "worker_type": "probe",
                "employee_backend": "hermes",
            },
        )
        before = connect(str(db_path))
        tickets_before = before.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        before.close()
        rejected = client.post(
            "/api/tickets",
            json={
                "title": "Unknown backend",
                "worker_type": "probe",
                "employee_backend": "missing-backend",
            },
        )

    assert defaulted.status_code == 200
    assert defaulted.json()["employee_backend"] == "claude"
    assert defaulted.json()["employee_launch_model"] == "probe-model"
    assert defaulted.json()["employee_launch_reasoning_effort"] == "probe-high"
    assert overridden.status_code == 200
    assert overridden.json()["employee_backend"] == "hermes"
    assert overridden.json()["employee_launch_model"] is None
    assert overridden.json()["employee_launch_reasoning_effort"] is None
    assert rejected.status_code == 400
    assert rejected.json()["error"]["code"] == "validation"
    check = connect(str(db_path))
    try:
        assert check.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == tickets_before
    finally:
        check.close()


def test_ticket_creation_defaults_to_today_and_current_sprint_but_preserves_explicit_backlog(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        _seed_sprint(conn)
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        defaulted = client.post(
            "/api/tickets", json={"title": "Default placement", "worker_type": "coding"}
        )
        explicit_backlog = client.post(
            "/api/tickets",
            json={
                "title": "Explicit backlog",
                "worker_type": "coding",
                "sprint_id": None,
            },
        )

    assert defaulted.status_code == explicit_backlog.status_code == 200
    assert defaulted.json()["sprint_id"] == "sp_edit"
    assert explicit_backlog.json()["sprint_id"] is None
    with TestClient(app) as client:
        assert client.get(f"/api/tickets/{defaulted.json()['id']}").json()["day_ids"] == [
            "day_2026-07-10"
        ]
        assert client.get(f"/api/tickets/{explicit_backlog.json()['id']}").json()["day_ids"] == [
            "day_2026-07-10"
        ]


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
            json=_employee_configuration_body("hermes"),
        )
        empty = client.put(
            f"/api/tickets/{empty_id}/employee-configuration",
            json=_employee_configuration_body("hermes"),
        )

    assert awaiting.status_code == empty.status_code == 200
    assert awaiting.json()["employee_backend"] == empty.json()["employee_backend"] == "hermes"
    assert awaiting.json()["employee_configuration_editable"] is True
    assert empty.json()["employee_configuration_editable"] is True
    check = connect(str(db_path))
    try:
        for ticket_id in (awaiting_id, empty_id):
            stored = tickets_data.read_ticket(check, ticket_id)
            assert stored.employee_backend == "hermes"
            assert stored.employee_launch_model is None
            assert stored.employee_launch_reasoning_effort is None
    finally:
        check.close()


def test_employee_configuration_stays_editable_when_kickoff_proposal_enters_discussion(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    # A kickoff proposal that a user starts discussing flips awaiting_approval ->
    # paired; the proposal is still filed and approvable, so Employee
    # configuration must stay editable exactly as it is at awaiting_approval.
    _app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)
    conn = connect(str(db_path))
    try:
        tickets_data.enter_paired_on_human_reply(conn, ticket_id, now=2)
        ticket = tickets_data.read_ticket(conn, ticket_id)
        assert ticket.ticket_status is TicketStatus.paired
        assert tickets_data.employee_configuration_editable(conn, ticket) is True
    finally:
        conn.close()


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
        async def snapshot(self, backend_key: str, *, refresh: bool = False) -> BackendSnapshot:
            del refresh
            return _backend_snapshot(
                backend_key,
                (
                    BackendModel(model_id="probe-a", reasoning_effort_options=("low", "high")),
                    BackendModel(model_id="probe-b", reasoning_effort_options=("low",)),
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
            json=_employee_configuration_body("claude", "probe-a", "high"),
        )
        model_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "probe-b", "high"),
        )
        backend_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes", "not-a-hermes-model", "extreme"),
        )
        switched_back = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "probe-a", "high"),
        )

    assert selected.status_code == 200
    assert (
        selected.json()["employee_launch_model"],
        selected.json()["employee_launch_reasoning_effort"],
    ) == ("probe-a", "high")
    assert model_changed.status_code == 200
    # probe-b takes only "low", and the model changed in the same write, so the effort
    # that no longer applies falls back to the new model's own rather than being refused.
    assert (
        model_changed.json()["employee_launch_model"],
        model_changed.json()["employee_launch_reasoning_effort"],
    ) == ("probe-b", None)
    assert backend_changed.status_code == 200
    assert (
        backend_changed.json()["employee_backend"],
        backend_changed.json()["employee_launch_model"],
        backend_changed.json()["employee_launch_reasoning_effort"],
    ) == ("hermes", None, None)
    assert switched_back.status_code == 200
    assert (
        switched_back.json()["employee_backend"],
        switched_back.json()["employee_launch_model"],
        switched_back.json()["employee_launch_reasoning_effort"],
    ) == ("claude", None, None)


def test_employee_configuration_refuses_a_model_the_backend_does_not_offer(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    class BackendSnapshots:
        async def snapshot(self, backend_key: str, *, refresh: bool = False) -> BackendSnapshot:
            del refresh
            return _backend_snapshot(
                backend_key,
                (BackendModel(model_id="probe-a", reasoning_effort_options=("low",)),),
                ("low",),
            )

    app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)

    with TestClient(app) as client:
        app.state.conversation = SimpleNamespace(backend_snapshots=BackendSnapshots())
        response = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "invented-model", None),
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
    assert response.json()["error"]["message"] == "Employee model is not available"


def test_employee_configuration_backend_probe_failure_is_a_retryable_product_error(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    class FailingBackendSnapshots:
        async def snapshot(self, backend_key: str, *, refresh: bool = False) -> BackendSnapshot:
            del backend_key, refresh
            raise RuntimeError("private adapter failure")

    app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)

    with TestClient(app) as client:
        app.state.conversation = SimpleNamespace(backend_snapshots=FailingBackendSnapshots())
        response = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude", "probe-a", "high"),
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "gateway_offline",
            "message": "the agent backends are unavailable",
            "detail": {},
        }
    }
    assert "private adapter failure" not in response.text


def test_employee_configuration_noop_after_freeze_emits_nothing(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path, worker_type="probe")
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body(
                "claude", "probe-model", "probe-high"
            ),
        )

    assert response.status_code == 200
    assert response.json()["employee_configuration_editable"] is False
    assert _snapshot(db_path, ticket_id) == before


@pytest.mark.parametrize(
    ("mutation_sql", "mutation_parameters"),
    (
        ("UPDATE tickets SET stage = 'needs_alpha' WHERE id = ?", ()),
        ("UPDATE tickets SET ticket_status = 'agent' WHERE id = ?", ()),
        ("UPDATE tickets SET ticket_status = 'user' WHERE id = ?", ()),
        ("UPDATE tickets SET ticket_status = 'errored' WHERE id = ?", ()),
        ("UPDATE tickets SET conversation_id = 'session-existing' WHERE id = ?", ()),
    ),
)
def test_employee_configuration_change_rejects_every_pristine_freeze_boundary(
    tmp_path: Path,
    probe_runtime: None,
    mutation_sql: str,
    mutation_parameters: tuple[object, ...],
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)
    conn = connect(str(db_path))
    conn.execute(mutation_sql, (*mutation_parameters, ticket_id))
    conn.close()
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes"),
        )

    assert response.status_code == 409
    assert _snapshot(db_path, ticket_id) == before


def test_employee_configuration_endpoint_requires_the_exact_complete_nullable_body(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)
    # A Ticket that names a conversation is frozen: its launch values are what that
    # conversation was started on, and there is no changing them after the fact.
    conn = connect(str(db_path))
    conn.execute(
        "UPDATE tickets SET conversation_id = ? WHERE id = ?",
        (f"conversation-for-{ticket_id}", ticket_id),
    )
    conn.commit()
    conn.close()
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        bound = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes"),
        )
        indirect = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("claude"),
            headers={"X-Plan-Actor": "worker"},
        )
        extra = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json={**_employee_configuration_body("claude"), "extra": True},
        )
        unknown = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("missing-backend"),
        )
        missing = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json={"employee_backend": "claude"},
        )
        wrong_nullable_type = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json={
                **_employee_configuration_body("claude"),
                "employee_launch_model": 42,
            },
        )
        detail = client.get(f"/api/tickets/{ticket_id}")

    assert bound.status_code == 409
    assert indirect.status_code == 400
    assert indirect.json()["error"]["code"] == "agent_forbidden"
    assert extra.status_code == 400
    assert unknown.status_code == 400
    assert missing.status_code == 400
    assert wrong_nullable_type.status_code == 400
    assert detail.status_code == 200
    assert detail.json()["employee_configuration_editable"] is False
    assert _snapshot(db_path, ticket_id) == before


def test_execution_route_is_absent_and_patch_rejects_it_as_unknown(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        detail = client.get(f"/api/tickets/{ticket_id}")
        assert detail.status_code == 200
        assert "execution_route" not in detail.json()
        copy_text = client.get(f"/api/tickets/{ticket_id}/copy-text")
        assert copy_text.status_code == 200
        assert "execution_route" not in copy_text.text

        response = client.patch(
            f"/api/tickets/{ticket_id}", json={"execution_route": "panels_worker"}
        )
        assert response.status_code == 400
        assert response.json()["error"] == {
            "code": "validation",
            "message": "unknown ticket field",
            "detail": {"field": "execution_route"},
        }
    assert _snapshot(db_path, ticket_id) == before


def test_compound_patch_rolls_back_when_late_sprint_validation_fails(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"title": "Must not land", "sprint_id": "sp_missing"},
        )

    assert response.status_code == 404
    assert response.json()["error"] == {
        "code": "not_found",
        "message": "sprint not found",
        "detail": {"sprint_id": "sp_missing"},
    }
    assert _snapshot(db_path, ticket_id) == before


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
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={
                "sprint_id": "sp_edit",
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
    assert response.json()["sprint_id"] == "sp_edit"
    assert before["values"] != _snapshot(db_path, ticket_id)["values"]
    assert _snapshot(db_path, ticket_id)["context"] == (
        (
            "ticket_changed",
            "This ticket changed outside your worker turn. Reread the ticket before continuing.",
            1,
        ),
    )
    transaction_statements = [statement.strip() for statement in trace]
    assert sum(statement == "BEGIN IMMEDIATE" for statement in transaction_statements) == 1
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
    assert any(
        "SELECT 1 FROM SPRINTS WHERE ID" in statement.upper() for statement in statements_under_lock
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
        sprint_id="sp_edit",
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
                "sprint_id": "sp_edit",
            },
        )

    assert response.status_code == 200, response.json()
    assert _snapshot(db_path, ticket_id) == before


def test_patch_of_existing_null_values_is_a_true_noop(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"deadline": None, "project": None, "project_id": None, "sprint_id": None},
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
        by_id = client.patch(f"/api/tickets/{id_id}", json={"project_id": "project_vylo"})
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


def test_rejected_compound_edits_preserve_existing_errors_and_have_no_effect(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        _seed_sprint(conn)
        conn.execute(
            "INSERT INTO sprint_items "
            "(id, title, project_id, sprint_id, created_at, updated_at) "
            "VALUES ('si_edit', 'Parent', 'project_vylo', 'sp_edit', 1, 1)"
        )
    finally:
        conn.close()

    cases = (
        (
            {"title": "Must not land", "project": "Other", "project_id": "project_vylo"},
            400,
            {
                "code": "validation",
                "message": "project_id and project do not match",
                "detail": {"project_id": "project_vylo", "project": "Other"},
            },
            {},
        ),
        (
            {"title": "Must not land", "project": "Missing project"},
            400,
            {
                "code": "validation",
                "message": "invalid project",
                "detail": {"project": "Missing project"},
            },
            {},
        ),
        (
            {"title": "Must not land", "project_id": "project_missing"},
            400,
            {
                "code": "validation",
                "message": "invalid project_id",
                "detail": {"project_id": "project_missing"},
            },
            {},
        ),
        (
            {"title": "Must not land", "sprint_id": "sp_missing"},
            404,
            {
                "code": "not_found",
                "message": "sprint not found",
                "detail": {"sprint_id": "sp_missing"},
            },
            {},
        ),
        (
            {"title": "Must not land", "deadline": "not-a-date"},
            400,
            {
                "code": "validation",
                "message": "deadline must be an ISO date",
                "detail": {"deadline": "not-a-date"},
            },
            {},
        ),
        (
            {"title": "", "priority": "P1"},
            400,
            {"code": "validation", "message": "title must be non-empty", "detail": {}},
            {},
        ),
        (
            {"title": "x" * 201},
            400,
            {
                "code": "title_too_long",
                "message": "title exceeds 200 characters",
                "detail": {"length": 201, "max": 200},
            },
            {},
        ),
        (
            {"title": "Must not land", "project": "Vylo"},
            400,
            {
                "code": "validation",
                "message": "project is derived when parented",
                "detail": {},
            },
            {"sprint_item_id": "si_edit"},
        ),
        (
            {"project": None},
            400,
            {
                "code": "validation",
                "message": "project is derived when parented",
                "detail": {},
            },
            {"sprint_item_id": "si_edit"},
        ),
        (
            {"title": "Must not land", "sprint_id": "sp_edit"},
            400,
            {
                "code": "sprint_derived",
                "message": "sprint_id is derived from the parent item",
                "detail": {"sprint_item_id": "si_edit"},
            },
            {"sprint_item_id": "si_edit"},
        ),
        (
            {"sprint_id": None},
            400,
            {
                "code": "sprint_derived",
                "message": "sprint_id is derived from the parent item",
                "detail": {"sprint_item_id": "si_edit"},
            },
            {"sprint_item_id": "si_edit"},
        ),
    )

    with TestClient(app) as client:
        for body, status, error, create_values in cases:
            ticket_id = _create_ticket(db_path, **create_values)
            if error["code"] == "sprint_derived":
                error["detail"]["ticket_id"] = ticket_id
            before = _snapshot(db_path, ticket_id)
            response = client.patch(f"/api/tickets/{ticket_id}", json=body)
            assert response.status_code == status, (body, response.json())
            assert response.json()["error"] == error
            assert _snapshot(db_path, ticket_id) == before


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
