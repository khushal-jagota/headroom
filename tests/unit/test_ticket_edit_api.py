"""Ordinary Ticket PATCH is one atomic edit through the real HTTP boundary."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.chat import data as chat_data
from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import Priority
from planner.core.db import connect, create_schema
from planner.core.events import read_events_since
from planner.core.server import create_app
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, FieldName
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
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        conn = connect(str(db_path))
        if trace is not None:
            conn.set_trace_callback(trace.append)
        return conn

    return create_app(config, clock, adapters, conn_factory), db_path


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
            worker_type="coding",
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
            field=FieldName.kickoff,
            actor="unattributed",
            now=1,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        return ticket.id
    finally:
        conn.close()


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
                ticket.implementer.value if ticket.implementer is not None else None,
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


def _new_ticket_events(db_path: Path, ticket_id: str, prior_count: int) -> list[Any]:
    conn = connect(str(db_path))
    try:
        return [
            event for event in read_events_since(conn, 0, 10_000) if event.entity_id == ticket_id
        ][prior_count:]
    finally:
        conn.close()


def test_patch_implementer_set_change_clear_noop_and_invalid_are_atomic(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    original = _snapshot(db_path, ticket_id)

    with TestClient(app) as client:
        set_response = client.patch(f"/api/tickets/{ticket_id}", json={"implementer": "khushal"})
        assert set_response.status_code == 200, set_response.json()
        assert set_response.json()["implementer"] == "khushal"
        assert set_response.json()["stage"] == "needs_success"
        assert set_response.json()["ticket_status"] == "empty"

        changed_response = client.patch(
            f"/api/tickets/{ticket_id}", json={"implementer": "hermes_codex"}
        )
        assert changed_response.status_code == 200, changed_response.json()
        assert changed_response.json()["implementer"] == "hermes_codex"
        detail = client.get(f"/api/tickets/{ticket_id}")
        assert detail.status_code == 200
        assert detail.json()["implementer"] == "hermes_codex"
        copy_text = client.get(f"/api/tickets/{ticket_id}/copy-text")
        assert copy_text.status_code == 200
        assert "implementer: hermes_codex\n" in copy_text.text

        cleared_response = client.patch(f"/api/tickets/{ticket_id}", json={"implementer": None})
        assert cleared_response.status_code == 200, cleared_response.json()
        assert cleared_response.json()["implementer"] is None
        cleared = _snapshot(db_path, ticket_id)

        noop_response = client.patch(f"/api/tickets/{ticket_id}", json={"implementer": None})
        assert noop_response.status_code == 200, noop_response.json()
        assert _snapshot(db_path, ticket_id) == cleared

        invalid_response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"title": "Must not land", "implementer": "other"},
        )
        assert invalid_response.status_code == 400
        assert invalid_response.json()["error"] == {
            "code": "validation",
            "message": "invalid implementer",
            "detail": {"implementer": "other"},
        }
        assert _snapshot(db_path, ticket_id) == cleared

        forbidden_response = client.patch(
            f"/api/tickets/{ticket_id}",
            json={"priority": "P1", "implementer": "panels_worker"},
            headers={"X-Plan-Actor": "agent"},
        )
        assert forbidden_response.status_code == 400
        assert forbidden_response.json()["error"] == {
            "code": "agent_forbidden",
            "message": "direct-only field",
            "detail": {"field": "implementer", "actor": "agent"},
        }

    final = _snapshot(db_path, ticket_id)
    assert final == cleared
    assert final["values"][-2:] == original["values"][-2:]
    assert [event[1] for event in final["events"][len(original["events"]) :]] == [
        {"field": "implementer", "from": None, "to": "khushal"},
        {"field": "implementer", "from": "khushal", "to": "hermes_codex"},
        {"field": "implementer", "from": "hermes_codex", "to": None},
    ]
    assert final["context"] == (
        (
            "ticket_changed",
            "This ticket changed outside your worker turn. Reread the ticket before continuing.",
            3,
        ),
    )


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


def test_active_worker_and_running_chat_do_not_block_an_ordinary_edit(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _create_ticket(db_path)
    conn = connect(str(db_path))
    try:
        started = tickets_data.start_run_if_runnable(conn, ticket_id, guard=None, now=2)
        assert started is not None
        chat_data.start_turn(
            conn,
            ticket_id,
            origin="human",
            mode="message",
            visible_role="human",
            visible_text="Still editing directly",
            output_role="assistant",
            phase="thinking",
            activity_label=None,
            now=3,
        )
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
