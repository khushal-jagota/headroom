from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import install_probe_registry, uninstall_probe_registry

from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.projects import data as projects_data
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    AtCap,
    StageOwnershipMode,
    TicketStatus,
)
from planner.tickets.data import (
    create_ticket,
    file_proposal,
    reconcile_ticket_from_external_work,
)
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

_CHIEF = {"X-Plan-Actor": "chief"}


@pytest.fixture
def probe_runtime() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
        },
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(
        config,
        build_clock(config),
        conn_factory,
        # Reconciling asks the conversation system whether a turn is running, so this file
        # needs one whose running turn it can start by hand and that spawns nothing.
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    return app, db_path


def _ordinary_ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Existing work",
            actor="unattributed",
            now=1,
            title_max_chars=200,
            kickoff_note="Original report",
        )
        return tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="unattributed",
            now=1,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        ).id
    finally:
        conn.close()


def _external_body(state: str, *, note: str = "External report and reasoning") -> dict[str, str]:
    body = {"stage": state, "kickoff_note": note}
    values = {
        "success": "Success settled",
        "approach": "Approach settled",
        "plan": "Plan settled",
        "implementation": "Implementation settled",
        "closeout": "Closeout settled",
    }
    prefix_count = {
        "needs_success": 0,
        "needs_approach": 1,
        "needs_plan": 2,
        "needs_implementation": 3,
        "needs_closeout": 4,
        "done": 5,
    }[state]
    body.update(dict(list(values.items())[:prefix_count]))
    return body


def _ticket_row(db_path: Path, ticket_id: str) -> tuple[object, ...]:
    conn = connect(str(db_path))
    try:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        assert row is not None
        return tuple(row)
    finally:
        conn.close()


def test_external_create_backend_default_override_and_unknown_before_mutation(
    tmp_path: Path,
    probe_runtime: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    base = {
        "worker_type": "probe",
        "stage": "needs_alpha",
        "kickoff_note": "External kickoff",
    }
    with TestClient(app) as client:
        defaulted = client.post(
            "/api/chief/tickets/from-external-work",
            json={"title": "External default", **base},
            headers=_CHIEF,
        )
        overridden = client.post(
            "/api/chief/tickets/from-external-work",
            json={"title": "External override", **base, "employee_backend": "hermes"},
            headers=_CHIEF,
        )
        conn = connect(str(db_path))
        tickets_before = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        conn.close()
        rejected = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "title": "External rejected",
                **base,
                "employee_backend": "missing-backend",
            },
            headers=_CHIEF,
        )

    assert defaulted.status_code == overridden.status_code == 200
    assert defaulted.json()["employee_backend"] == "claude"
    assert overridden.json()["employee_backend"] == "hermes"
    assert rejected.status_code == 400
    check = connect(str(db_path))
    try:
        assert check.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == tickets_before
    finally:
        check.close()
    assert defaulted.json()["employee_launch_model"] == "probe-model"
    assert defaulted.json()["employee_launch_reasoning_effort"] == "probe-high"
    assert overridden.json()["employee_launch_model"] is None
    assert overridden.json()["employee_launch_reasoning_effort"] is None


@pytest.mark.parametrize("headers", [{}, {"X-Plan-Actor": "worker"}, {"X-Plan-Actor": "agent"}])
def test_both_external_work_routes_require_explicit_chief(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    with TestClient(app) as client:
        reconciled = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body("needs_success"),
            headers=headers,
        )
        created = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "title": "Imported",
                "worker_type": "coding",
                **_external_body("needs_success"),
            },
            headers=headers,
        )
    assert reconciled.status_code == 400
    assert reconciled.json()["error"]["code"] == "agent_forbidden"
    assert created.status_code == 400
    assert created.json()["error"]["code"] == "agent_forbidden"


@pytest.mark.parametrize("state", CODING_WORKER_TYPE_DEFINITION.stage_ids()[1:])
def test_create_external_work_enforces_exact_settled_prefix_and_coherent_control(
    tmp_path: Path, state: str
) -> None:
    app, _db_path = _make_app(tmp_path)
    body = {
        "title": f"Imported {state}",
        "worker_type": "coding",
        "recap": "Imported recap",
        **_external_body(state),
    }
    with TestClient(app) as client:
        response = client.post("/api/chief/tickets/from-external-work", json=body, headers=_CHIEF)
    assert response.status_code == 200, response.json()
    ticket = response.json()
    assert ticket["stage"] == state
    assert ticket["ceiling"] == state
    assert ticket["at_cap"] == "propose"
    assert ticket["ticket_status"] == "empty"
    expected = {
        key: body.get(key) for key in ("success", "approach", "plan", "implementation", "closeout")
    }
    assert {key: ticket["fields"][key]["value"] for key in expected} == expected


def test_reconcile_external_work_preserves_explicit_stop(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET at_cap = 'stop' WHERE id = ?", (ticket_id,))
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body("needs_plan"),
            headers=_CHIEF,
        )

    assert response.status_code == 200, response.json()
    ticket = response.json()
    assert ticket["ceiling"] == "needs_plan"
    assert ticket["at_cap"] == "stop"


def test_external_work_rejects_unknown_keys_and_prefix_mismatches_without_writes(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    before = _ticket_row(db_path, ticket_id)
    with TestClient(app) as client:
        for forbidden in ("proposal", "fields", "ceiling", "at_cap", "ticket_status", "wat"):
            response = client.post(
                f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
                json={**_external_body("needs_success"), forbidden: "no"},
                headers=_CHIEF,
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "validation"
        missing = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={"stage": "needs_plan", "kickoff_note": "note", "success": "yes"},
            headers=_CHIEF,
        )
        future = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={**_external_body("needs_success"), "implementation": "too early"},
            headers=_CHIEF,
        )
        dropped = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={"stage": "dropped", "kickoff_note": "note"},
            headers=_CHIEF,
        )
    assert [missing.status_code, future.status_code, dropped.status_code] == [400, 400, 400]
    assert _ticket_row(db_path, ticket_id) == before


def test_reconcile_rejects_backward_pending_active_control_and_running_turn(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        made = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "title": "Forward",
                "worker_type": "coding",
                **_external_body("needs_implementation"),
            },
            headers=_CHIEF,
        ).json()
        backward = client.post(
            f"/api/chief/tickets/{made['id']}/reconcile-from-external-work",
            json=_external_body("needs_plan"),
            headers=_CHIEF,
        )
    assert backward.status_code == 400

    pending_id = _ordinary_ticket(db_path)
    conn = connect(str(db_path))
    try:
        file_proposal(
            conn,
            pending_id,
            field="success",
            body="pending",
            actor="worker",
            now=2,
        )
        conn.execute("UPDATE tickets SET ticket_status = 'empty' WHERE id = ?", (pending_id,))
        conn.commit()
    finally:
        conn.close()
    with TestClient(app) as client:
        pending = client.post(
            f"/api/chief/tickets/{pending_id}/reconcile-from-external-work",
            json=_external_body("needs_success"),
            headers=_CHIEF,
        )
    assert pending.status_code == 400

    for status in ("agent", "awaiting_approval"):
        ticket_id = _ordinary_ticket(db_path)
        conn = connect(str(db_path))
        try:
            conn.execute("UPDATE tickets SET ticket_status = ? WHERE id = ?", (status, ticket_id))
            conn.commit()
        finally:
            conn.close()
        with TestClient(app) as client:
            response = client.post(
                f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
                json=_external_body("needs_success"),
                headers=_CHIEF,
            )
        assert response.status_code == 409

    # A live conversation is the conversation system's fact, so the route asks it before
    # the writer runs: a Ticket whose worker is mid-turn is refused even at an admitted
    # status like `paired`.
    live_id = _ordinary_ticket(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            ("conv-live", live_id),
        )
        conn.commit()
    finally:
        conn.close()
    with TestClient(app) as client:
        asyncio.run(
            app.state.conversation_system.start_conversation(
                ConversationStartRequest(conversation_id="conv-live")
            )
        )
        asyncio.run(
            app.state.conversation_system.send(
                "conv-live",
                text_message_content("working"),
                sender_label="loop",
            )
        )
        running = client.post(
            f"/api/chief/tickets/{live_id}/reconcile-from-external-work",
            json=_external_body("needs_success"),
            headers=_CHIEF,
        )
    assert running.status_code == 409
    assert running.json()["error"]["code"] == "already_running"


def test_reconcile_is_atomic_and_normalizes_an_errored_ticket(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET ticket_status = 'errored' WHERE id = ?", (ticket_id,))
        conn.commit()
    finally:
        conn.close()

    before = _ticket_row(db_path, ticket_id)

    with TestClient(app) as client:
        invalid = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={
                "stage": "needs_plan",
                "kickoff_note": "new complete note",
                "success": "valid first field",
                "approach": "",
            },
            headers=_CHIEF,
        )
        assert invalid.status_code == 400
        assert _ticket_row(db_path, ticket_id) == before
        response = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={
                "recap": "recap",
                **_external_body("needs_plan", note="new complete note"),
            },
            headers=_CHIEF,
        )
    assert response.status_code == 200, response.json()
    ticket = response.json()
    assert ticket["ticket_status"] == "empty"
    assert ticket["ceiling"] == "needs_plan"
    assert ticket["at_cap"] == "propose"
    assert ticket["stage"] == "needs_plan"
    assert ticket["recap"] == "recap"
    assert [
        ticket["fields"][field]["value"] for field in ("kickoff", "success", "approach")
    ] == ["new complete note", "Success settled", "Approach settled"]


def test_create_external_work_settles_every_provided_field(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "title": "Already done",
                "worker_type": "coding",
                "recap": "done elsewhere",
                **_external_body("done"),
            },
            headers=_CHIEF,
        )
    assert response.status_code == 200, response.json()
    created = response.json()
    assert created["stage"] == "done"
    assert created["ceiling"] == "done"
    assert created["at_cap"] == "propose"
    assert created["recap"] == "done elsewhere"
    assert [
        created["fields"][field]["value"]
        for field in ("success", "approach", "plan", "implementation", "closeout")
    ] == [
        "Success settled",
        "Approach settled",
        "Plan settled",
        "Implementation settled",
        "Closeout settled",
    ]


def test_reconcile_safety_reads_happen_after_begin_immediate(tmp_path: Path) -> None:
    _app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    lock_holder = connect(str(db_path))
    attempted_begin = threading.Event()
    outcome: queue.Queue[object] = queue.Queue()

    lock_holder.execute("BEGIN IMMEDIATE")
    lock_holder.execute(
        "UPDATE tickets SET ticket_status = 'agent' WHERE id = ?",
        (ticket_id,),
    )

    def reconcile_after_lock() -> None:
        conn = connect(str(db_path))
        conn.set_trace_callback(
            lambda statement: (
                attempted_begin.set() if statement.strip().upper() == "BEGIN IMMEDIATE" else None
            )
        )
        try:
            reconcile_ticket_from_external_work(
                conn,
                ticket_id,
                kickoff_note="External report",
                target_stage="needs_success",
                provided_values={},
                actor="chief",
                now=3,
            )
        except Exception as exc:  # the assertion below checks the exact domain error
            outcome.put(exc)
        else:
            outcome.put(None)
        finally:
            conn.close()

    worker = threading.Thread(target=reconcile_after_lock)
    worker.start()
    assert attempted_begin.wait(timeout=2)
    lock_holder.commit()
    lock_holder.close()
    worker.join(timeout=5)
    assert not worker.is_alive()

    caught = outcome.get_nowait()
    assert isinstance(caught, PlannerError)
    assert caught.code is ErrorCode.already_running
    conn = connect(str(db_path))
    try:
        ticket = conn.execute(
            "SELECT stage, fields, ticket_status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        assert ticket["stage"] == "needs_success"
        assert json.loads(ticket["fields"])["kickoff"]["value"] == "Original report"
        assert ticket["ticket_status"] == "agent"
    finally:
        conn.close()


def test_reconcile_current_paired_stage_preserves_resting_status(tmp_path: Path) -> None:
    _app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.set_stage_ownership(
            conn,
            ticket_id,
            stage="needs_success",
            ownership_mode=StageOwnershipMode.paired,
            now=2,
        )
        assert ticket.ticket_status is TicketStatus.empty
        conn.execute(
            "UPDATE tickets SET ticket_status = 'paired', conversation_id = ? "
            "WHERE id = ?",
            ("paired-session", ticket_id),
        )
        conn.commit()

        reconciled = reconcile_ticket_from_external_work(
            conn,
            ticket_id,
            target_stage="needs_success",
            provided_values={},
            actor="chief",
            kickoff_note="External report",
            now=3,
        )

        assert reconciled.stage == "needs_success"
        assert reconciled.ticket_status is TicketStatus.paired
        assert reconciled.conversation_id == "paired-session"
    finally:
        conn.close()


def test_external_create_and_reconcile_keep_the_parent_item_link(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        project = projects_data.create_project(conn, name="External project", now=1)
        item = sprints_data.create_item(
            conn,
            title="External item",
            project_id=project.id,
            clock=RealClock(),
        )
    finally:
        conn.close()

    with TestClient(app) as client:
        created_response = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "title": "Parented external work",
                "worker_type": "coding",
                "sprint_item_id": item.id,
                **_external_body("needs_success"),
            },
            headers=_CHIEF,
        )
    assert created_response.status_code == 200, created_response.json()
    ticket_id = created_response.json()["id"]
    assert created_response.json()["sprint_item_id"] == item.id

    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET ticket_status = 'errored' WHERE id = ?", (ticket_id,))
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        reconciled_response = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body("needs_approach"),
            headers=_CHIEF,
        )
    assert reconciled_response.status_code == 200, reconciled_response.json()
    assert reconciled_response.json()["sprint_item_id"] == item.id
    assert reconciled_response.json()["stage"] == "needs_approach"
    assert reconciled_response.json()["ticket_status"] == "empty"
