"""Ordinary Ticket PATCH is one atomic edit through the real HTTP boundary."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import (
    PROBE_EMPLOYEE_BACKEND_CATALOG,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.conversation import sqlite_binding_repository as binding_repository_module
from planner.conversation.contracts import ConversationSessionBinding
from planner.conversation.employee_configuration import (
    EmployeeConfigurationCatalog,
    EmployeeConfigurationCatalogOption,
)
from planner.conversation.sqlite_binding_repository import SqliteConversationBindingRepository
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import Priority
from planner.core.db import connect, create_schema
from planner.core.events import read_events_since
from planner.core.server import create_app
from planner.days import data as days_data
from planner.runtime import automatic_employee_step_eligibility
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    EmployeeLaunchConfiguration,
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
            "events": tuple(
                (event.kind, event.payload, event.created_at)
                for event in read_events_since(conn, 0, 10_000)
                if event.entity_id == ticket_id
            ),
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
        counts_before = tuple(
            before.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("tickets", "events")
        )
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
    assert defaulted.json()["employee_backend"] == "probe-backend"
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
        assert (
            tuple(
                check.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("tickets", "events")
            )
            == counts_before
        )
        created_payloads = [
            event.payload
            for event in read_events_since(check, 0, 10_000)
            if event.kind == "ticket_created"
        ]
        assert [payload["employee_backend"] for payload in created_payloads] == [
            "probe-backend",
            "hermes",
        ]
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


def test_employee_configuration_endpoint_allows_pristine_statuses_and_emits_exact_event(
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
            events = [
                event
                for event in read_events_since(check, 0, 10_000)
                if event.entity_id == ticket_id and event.kind == "ticket_updated"
            ]
            assert [event.payload for event in events] == [
                {
                    "field": "employee_configuration",
                    "from": _employee_configuration_body(
                        "probe-backend", "probe-model", "probe-high"
                    ),
                    "to": _employee_configuration_body("hermes"),
                }
            ]
    finally:
        check.close()


def test_employee_configuration_stays_editable_when_kickoff_proposal_enters_discussion(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    # A kickoff proposal that a user starts discussing flips awaiting_approval ->
    # proposal_discussion; the proposal is still filed and approvable, so Employee
    # configuration must stay editable exactly as it is at awaiting_approval.
    _app, db_path = _make_app(tmp_path)
    ticket_id = _create_pristine_ticket(db_path)
    conn = connect(str(db_path))
    try:
        tickets_data.enter_proposal_discussion(conn, ticket_id, now=2)
        ticket = tickets_data.read_ticket(conn, ticket_id)
        assert ticket.ticket_status is TicketStatus.proposal_discussion
        assert tickets_data.employee_configuration_editable(conn, ticket) is True
    finally:
        conn.close()


def test_employee_configuration_writer_normalizes_worker_and_model_dependencies(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    class CatalogService:
        async def catalog(self, employee_backend: str, candidate_model: str | None):
            reasoning_values = ("low",) if candidate_model == "probe-b" else ("low", "high")
            return SimpleNamespace(
                models=tuple(
                    SimpleNamespace(value=value) for value in ("probe-a", "probe-b")
                ),
                reasoning_supported=True,
                reasoning_efforts=tuple(
                    SimpleNamespace(value=value) for value in reasoning_values
                ),
            )

    app, db_path = _make_app(tmp_path)
    app.state.conversation = SimpleNamespace(
        employee_configuration_catalog=CatalogService()
    )
    ticket_id = _create_pristine_ticket(db_path)

    with TestClient(app) as client:
        selected = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("probe-backend", "probe-a", "high"),
        )
        model_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("probe-backend", "probe-b", "high"),
        )
        backend_changed = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes", "not-a-hermes-model", "extreme"),
        )
        switched_back = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("probe-backend", "probe-a", "high"),
        )

    assert selected.status_code == 200
    assert (
        selected.json()["employee_launch_model"],
        selected.json()["employee_launch_reasoning_effort"],
    ) == ("probe-a", "high")
    assert model_changed.status_code == 200
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
    ) == ("probe-backend", None, None)


def test_employee_configuration_catalog_failure_is_a_retryable_product_error(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    class FailingCatalogService:
        async def catalog(self, employee_backend: str, candidate_model: str | None):
            del employee_backend, candidate_model
            raise RuntimeError("private adapter failure")

    app, _db_path = _make_app(tmp_path)
    app.state.conversation = SimpleNamespace(
        employee_configuration_catalog=FailingCatalogService()
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/employee-configuration-catalog",
            params={"employee_backend": "probe-backend"},
        )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "gateway_offline",
            "message": "employee configuration catalog is unavailable",
            "detail": {},
        }
    }
    assert "private adapter failure" not in response.text


def test_employee_configuration_catalog_endpoint_serves_the_exact_product_shape(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    expected = EmployeeConfigurationCatalog(
        employee_backend="probe-backend",
        candidate_model="probe-model",
        native_model="probe-native",
        models=(EmployeeConfigurationCatalogOption(value="probe-model", label="Probe"),),
        reasoning_supported=True,
        native_reasoning_effort="probe-high",
        reasoning_efforts=(
            EmployeeConfigurationCatalogOption(value="probe-high", label="High"),
        ),
    )

    class CatalogService:
        async def catalog(self, employee_backend: str, candidate_model: str | None):
            assert (employee_backend, candidate_model) == (
                "probe-backend",
                "probe-model",
            )
            return expected

    app, _db_path = _make_app(tmp_path)
    app.state.conversation = SimpleNamespace(
        employee_configuration_catalog=CatalogService()
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/employee-configuration-catalog",
            params={
                "employee_backend": "probe-backend",
                "candidate_model": "probe-model",
            },
        )

    assert response.status_code == 200
    assert response.json() == expected.model_dump(mode="json")


def test_employee_configuration_catalog_endpoint_forwards_deliberate_refresh(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    class CatalogService:
        async def catalog(
            self,
            employee_backend: str,
            candidate_model: str | None,
            *,
            force_refresh: bool = False,
        ) -> EmployeeConfigurationCatalog:
            assert (employee_backend, candidate_model, force_refresh) == (
                "probe-backend",
                None,
                True,
            )
            return EmployeeConfigurationCatalog(
                employee_backend=employee_backend,
                candidate_model=candidate_model,
                native_model=None,
                models=(),
                reasoning_supported=False,
                native_reasoning_effort=None,
                reasoning_efforts=(),
            )

    app, _db_path = _make_app(tmp_path)
    app.state.conversation = SimpleNamespace(
        employee_configuration_catalog=CatalogService()
    )
    with TestClient(app) as client:
        response = client.get(
            "/api/employee-configuration-catalog",
            params={"employee_backend": "probe-backend", "force_refresh": "true"},
        )

    assert response.status_code == 200


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
                "probe-backend", "probe-model", "probe-high"
            ),
        )

    assert response.status_code == 200
    assert response.json()["employee_configuration_editable"] is False
    assert _snapshot(db_path, ticket_id) == before


@pytest.mark.parametrize(
    ("mutation_sql", "mutation_parameters"),
    (
        ("UPDATE tickets SET stage = 'needs_alpha' WHERE id = ?", ()),
        ("UPDATE tickets SET ticket_status = 'agent_running_step' WHERE id = ?", ()),
        ("UPDATE tickets SET ticket_status = 'user_takeover' WHERE id = ?", ()),
        ("UPDATE tickets SET ticket_status = 'paired_work' WHERE id = ?", ()),
        ("UPDATE tickets SET ticket_status = 'errored' WHERE id = ?", ()),
        ("UPDATE tickets SET employee_session_id = 'session-existing' WHERE id = ?", ()),
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
    conn = connect(str(db_path))
    conn.execute(
        "INSERT INTO conversation_session_bindings "
        "(employee_id, entity_kind, entity_id, acp_session_id, backend_key, "
        "binding_generation, created_at, updated_at) VALUES (?, 'ticket', ?, "
        "'session-bound', 'probe-backend', 1, 1, 1)",
        (ticket_id, ticket_id),
    )
    conn.close()
    before = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        bound = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("hermes"),
        )
        indirect = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("probe-backend"),
            headers={"X-Plan-Actor": "worker"},
        )
        extra = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json={**_employee_configuration_body("probe-backend"), "extra": True},
        )
        unknown = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=_employee_configuration_body("missing-backend"),
        )
        missing = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json={"employee_backend": "probe-backend"},
        )
        wrong_nullable_type = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json={
                **_employee_configuration_body("probe-backend"),
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


def test_employee_configuration_writer_and_first_binding_race_in_both_commit_orders(
    tmp_path: Path,
    probe_runtime: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def prepare(
        name: str,
    ) -> tuple[
        Path,
        str,
        ConversationSessionBinding,
        EmployeeLaunchConfiguration,
    ]:
        db_path = tmp_path / f"{name}.db"
        conn = connect(str(db_path))
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            title=name,
            worker_type="probe",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        prepared_configuration = tickets_data.employee_launch_configuration(ticket)
        conn.close()
        return (
            db_path,
            ticket.id,
            ConversationSessionBinding(
                employee_id=ticket.id,
                acp_session_id=f"session-{name}",
                backend_key="probe-backend",
                binding_generation=1,
            ),
            prepared_configuration,
        )

    (
        writer_first_path,
        writer_first_id,
        writer_first_candidate,
        writer_first_prepared,
    ) = prepare("writer-first")
    cas_waiting = threading.Event()
    release_cas = threading.Event()
    real_binding_connect = binding_repository_module.connect

    def paused_binding_connect(*args, **kwargs):
        conn = real_binding_connect(*args, **kwargs)
        paused = False

        def trace(statement: str) -> None:
            nonlocal paused
            if statement == "BEGIN IMMEDIATE" and not paused:
                paused = True
                cas_waiting.set()
                assert release_cas.wait(2)

        conn.set_trace_callback(trace)
        return conn

    writer_first_repository = SqliteConversationBindingRepository(
        str(writer_first_path),
        workspace_root=tmp_path,
        integer_now=lambda: 2,
        employee_backend_catalog=PROBE_EMPLOYEE_BACKEND_CATALOG,
        chief_backend_key="hermes",
    )
    cas_errors: list[BaseException] = []
    with monkeypatch.context() as patch:
        patch.setattr(binding_repository_module, "connect", paused_binding_connect)

        def bind_after_writer() -> None:
            try:
                asyncio.run(
                    writer_first_repository.compare_and_swap_initial(
                        writer_first_candidate,
                        writer_first_prepared,
                    )
                )
            except BaseException as error:
                cas_errors.append(error)

        cas_thread = threading.Thread(target=bind_after_writer)
        cas_thread.start()
        assert cas_waiting.wait(2)
        writer_conn = connect(str(writer_first_path))
        tickets_data.write_employee_configuration(
            writer_conn,
            writer_first_id,
            expected_employee_configuration=writer_first_prepared,
            employee_backend="hermes",
            employee_launch_model=None,
            employee_launch_reasoning_effort=None,
            employee_backend_catalog=PROBE_EMPLOYEE_BACKEND_CATALOG,
            advertised_models=None,
            reasoning_supported=None,
            advertised_reasoning_efforts=None,
            now=2,
        )
        writer_conn.close()
        release_cas.set()
        cas_thread.join(2)
    assert not cas_thread.is_alive()
    assert len(cas_errors) == 1
    writer_first_check = connect(str(writer_first_path))
    assert (
        tuple(
            writer_first_check.execute(
                "SELECT employee_backend, employee_launch_model, "
                "employee_launch_reasoning_effort FROM tickets WHERE id = ?",
                (writer_first_id,),
            ).fetchone()
        )
        == ("hermes", None, None)
    )
    assert (
        writer_first_check.execute(
            "SELECT 1 FROM conversation_session_bindings WHERE employee_id = ?",
            (writer_first_id,),
        ).fetchone()
        is None
    )
    writer_first_check.close()

    (
        binding_first_path,
        binding_first_id,
        binding_first_candidate,
        binding_first_prepared,
    ) = prepare("binding-first")
    writer_waiting = threading.Event()
    release_writer = threading.Event()
    writer_paused = False

    def pause_writer(statement: str) -> None:
        nonlocal writer_paused
        if statement == "BEGIN IMMEDIATE" and not writer_paused:
            writer_paused = True
            writer_waiting.set()
            assert release_writer.wait(2)

    writer_errors: list[BaseException] = []

    def write_after_binding() -> None:
        writer_conn = connect(str(binding_first_path))
        writer_conn.set_trace_callback(pause_writer)
        try:
            tickets_data.write_employee_configuration(
                writer_conn,
                binding_first_id,
                expected_employee_configuration=binding_first_prepared,
                employee_backend="hermes",
                employee_launch_model=None,
                employee_launch_reasoning_effort=None,
                employee_backend_catalog=PROBE_EMPLOYEE_BACKEND_CATALOG,
                advertised_models=None,
                reasoning_supported=None,
                advertised_reasoning_efforts=None,
                now=2,
            )
        except BaseException as error:
            writer_errors.append(error)
        finally:
            writer_conn.close()

    writer_thread = threading.Thread(target=write_after_binding)
    writer_thread.start()
    assert writer_waiting.wait(2)
    binding_first_repository = SqliteConversationBindingRepository(
        str(binding_first_path),
        workspace_root=tmp_path,
        integer_now=lambda: 2,
        employee_backend_catalog=PROBE_EMPLOYEE_BACKEND_CATALOG,
        chief_backend_key="hermes",
    )
    assert (
        asyncio.run(
            binding_first_repository.compare_and_swap_initial(
                binding_first_candidate,
                binding_first_prepared,
            )
        )
        == binding_first_candidate
    )
    release_writer.set()
    writer_thread.join(2)
    assert not writer_thread.is_alive()
    assert len(writer_errors) == 1
    binding_first_check = connect(str(binding_first_path))
    row = binding_first_check.execute(
        "SELECT tickets.employee_backend, tickets.employee_launch_model, "
        "tickets.employee_launch_reasoning_effort, "
        "conversation_session_bindings.backend_key "
        "FROM tickets JOIN conversation_session_bindings "
        "ON conversation_session_bindings.employee_id = tickets.id WHERE tickets.id = ?",
        (binding_first_id,),
    ).fetchone()
    assert tuple(row) == (
        "probe-backend",
        binding_first_prepared.employee_launch_model,
        binding_first_prepared.employee_launch_reasoning_effort,
        "probe-backend",
    )
    binding_first_check.close()


def _new_ticket_events(db_path: Path, ticket_id: str, prior_count: int) -> list[Any]:
    conn = connect(str(db_path))
    try:
        return [
            event for event in read_events_since(conn, 0, 10_000) if event.entity_id == ticket_id
        ][prior_count:]
    finally:
        conn.close()


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
    events = _new_ticket_events(db_path, ticket_id, len(before["events"]))
    assert [(event.kind, event.payload) for event in events] == [
        (
            "ticket_updated",
            {"field": "title", "from": "Before edit", "to": "After edit"},
        ),
        (
            "ticket_updated",
            {"field": "priority", "from": "P3", "to": "P1"},
        ),
        (
            "ticket_updated",
            {"field": "deadline", "from": None, "to": "2026-08-01"},
        ),
        (
            "ticket_updated",
            {"field": "project_id", "from": None, "to": "project_vylo"},
        ),
        (
            "ticket_updated",
            {"field": "sprint_id", "from": None, "to": "sp_edit"},
        ),
    ]
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


def test_compound_patch_rolls_back_row_events_and_context_after_event_insert_fails(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute(
            "CREATE TRIGGER abort_priority_ticket_event "
            "BEFORE INSERT ON events "
            "WHEN NEW.kind = 'ticket_updated' "
            "AND json_extract(NEW.payload, '$.field') = 'priority' "
            "BEGIN SELECT RAISE(ABORT, 'forced ticket event failure'); END"
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


def test_active_worker_and_running_employee_step_do_not_block_an_ordinary_edit(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    conn = connect(str(db_path))
    try:
        planning_day_id = "day_2026-07-10"
        days_data.add_day_ticket(conn, planning_day_id, ticket_id, 2)
        started = tickets_data.claim_automatic_employee_step(
            conn,
            ticket_id,
            planning_day_id_resolver=lambda: planning_day_id,
            eligibility_check=(
                automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
            ),
            now=2,
        )
        assert started is not None
        SqliteEmployeeStepRepository().start(conn, ticket_id, now=3)
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"priority": "P1", "title": "Edited during active work"},
        )

    assert response.status_code == 200, response.json()
    assert response.json()["ticket_status"] == "agent_running_step"
    assert response.json()["title"] == "Edited during active work"
    assert response.json()["priority"] == "P1"
    assert _snapshot(db_path, ticket_id)["context"][-1][2] == 1
