from __future__ import annotations

import json
import queue
import threading
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.core.adapters.registry import build_adapters
from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.projects import data as projects_data
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, WORKER_STATE_ORDER, AtCap, FieldName, TicketState
from planner.tickets.data import (
    create_ticket,
    file_proposal,
    reconcile_ticket_from_external_work,
)

_CHIEF = {"X-Plan-Actor": "chief"}


def _make_app(tmp_path: Path):
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(config, build_clock(config), build_adapters(config), conn_factory)
    return app, db_path


def _ordinary_ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            title="Existing work",
            actor="unattributed",
            now=1,
            title_max_chars=200,
            kickoff_note="Original report",
        )
        return tickets_data.accept_proposal(
            conn,
            ticket.id,
            field=FieldName.kickoff,
            actor="unattributed",
            now=1,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        ).id
    finally:
        conn.close()


def _external_body(
    state: TicketState, *, note: str = "External report and reasoning"
) -> dict[str, str]:
    body = {"state": state.value, "kickoff_note": note}
    values = {
        "success": "Success settled",
        "approach": "Approach settled",
        "plan": "Plan settled",
        "implementation": "Implementation settled",
        "closeout": "Closeout settled",
    }
    prefix_count = {
        TicketState.needs_success: 0,
        TicketState.needs_approach: 1,
        TicketState.needs_plan: 2,
        TicketState.needs_implementation: 3,
        TicketState.needs_closeout: 4,
        TicketState.done: 5,
    }[state]
    body.update(dict(list(values.items())[:prefix_count]))
    return body


def _events(db_path: Path, ticket_id: str) -> list[tuple[str, dict]]:
    conn = connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT kind, payload FROM events WHERE entity_id = ? ORDER BY id", (ticket_id,)
        ).fetchall()
        return [(str(row["kind"]), json.loads(row["payload"])) for row in rows]
    finally:
        conn.close()


@pytest.mark.parametrize("headers", [{}, {"X-Plan-Actor": "worker"}, {"X-Plan-Actor": "agent"}])
def test_both_external_work_routes_require_explicit_chief(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    with TestClient(app) as client:
        reconciled = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body(TicketState.needs_success),
            headers=headers,
        )
        created = client.post(
            "/api/chief/tickets/from-external-work",
            json={"title": "Imported", **_external_body(TicketState.needs_success)},
            headers=headers,
        )
    assert reconciled.status_code == 400
    assert reconciled.json()["error"]["code"] == "agent_forbidden"
    assert created.status_code == 400
    assert created.json()["error"]["code"] == "agent_forbidden"


@pytest.mark.parametrize("state", WORKER_STATE_ORDER)
def test_create_external_work_enforces_exact_settled_prefix_and_coherent_control(
    tmp_path: Path, state: TicketState
) -> None:
    app, _db_path = _make_app(tmp_path)
    body = {"title": f"Imported {state.value}", "recap": "Imported recap", **_external_body(state)}
    with TestClient(app) as client:
        response = client.post(
            "/api/chief/tickets/from-external-work", json=body, headers=_CHIEF
        )
    assert response.status_code == 200, response.json()
    ticket = response.json()
    assert ticket["state"] == state.value
    assert ticket["ceiling"] == state.value
    assert ticket["at_cap"] == "stop"
    assert ticket["ticket_status"] == "empty"
    expected = {
        key: body.get(key) for key in ("success", "approach", "plan", "implementation", "closeout")
    }
    assert {key: ticket["fields"][key]["value"] for key in expected} == expected


def test_external_work_rejects_unknown_keys_and_prefix_mismatches_without_writes(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    before = _events(db_path, ticket_id)
    with TestClient(app) as client:
        for forbidden in ("proposal", "fields", "ceiling", "at_cap", "ticket_status", "wat"):
            response = client.post(
                f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
                json={**_external_body(TicketState.needs_success), forbidden: "no"},
                headers=_CHIEF,
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "validation"
        missing = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={"state": "needs_plan", "kickoff_note": "note", "success": "yes"},
            headers=_CHIEF,
        )
        future = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={**_external_body(TicketState.needs_success), "implementation": "too early"},
            headers=_CHIEF,
        )
        dropped = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={"state": "dropped", "kickoff_note": "note"},
            headers=_CHIEF,
        )
    assert [missing.status_code, future.status_code, dropped.status_code] == [400, 400, 400]
    assert _events(db_path, ticket_id) == before


def test_reconcile_rejects_backward_pending_active_control_and_running_turn(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        made = client.post(
            "/api/chief/tickets/from-external-work",
            json={"title": "Forward", **_external_body(TicketState.needs_implementation)},
            headers=_CHIEF,
        ).json()
        backward = client.post(
            f"/api/chief/tickets/{made['id']}/reconcile-from-external-work",
            json=_external_body(TicketState.needs_plan),
            headers=_CHIEF,
        )
    assert backward.status_code == 400

    pending_id = _ordinary_ticket(db_path)
    conn = connect(str(db_path))
    try:
        file_proposal(
            conn,
            pending_id,
            field=FieldName.success,
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
            json=_external_body(TicketState.needs_success),
            headers=_CHIEF,
        )
    assert pending.status_code == 400

    for status in ("agent_running_step", "awaiting_approval", "user_takeover"):
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
                json=_external_body(TicketState.needs_success),
                headers=_CHIEF,
            )
        assert response.status_code == 409

    running_id = _ordinary_ticket(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO chat_turns (id, entity_id, origin, mode, status, phase, output_role, "
            "started_at, updated_at) VALUES ('turn_external', ?, 'human', 'message', 'running', "
            "'doing', 'assistant', 1, 1)",
            (running_id,),
        )
        conn.commit()
    finally:
        conn.close()
    with TestClient(app) as client:
        running = client.post(
            f"/api/chief/tickets/{running_id}/reconcile-from-external-work",
            json=_external_body(TicketState.needs_success),
            headers=_CHIEF,
        )
    assert running.status_code == 409


def test_reconcile_is_atomic_normalizes_errored_and_emits_exact_existing_events(
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

    before = _events(db_path, ticket_id)
    class Rings:
        count = 0
        def ring(self) -> None:
            self.count += 1
    rings = Rings()
    app.state.readiness_doorbell = rings
    with TestClient(app) as client:
        invalid = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={
                "state": "needs_plan",
                "kickoff_note": "new complete note",
                "success": "valid first field",
                "approach": "",
            },
            headers=_CHIEF,
        )
        assert invalid.status_code == 400
        assert _events(db_path, ticket_id) == before
        response = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={
                "recap": "recap",
                **_external_body(TicketState.needs_plan, note="new complete note"),
            },
            headers=_CHIEF,
        )
    assert response.status_code == 200, response.json()
    ticket = response.json()
    assert ticket["ticket_status"] == "empty"
    assert ticket["ceiling"] == "needs_plan"
    assert ticket["at_cap"] == "stop"
    assert rings.count == 1
    new_events = _events(db_path, ticket_id)[len(before):]
    assert [kind for kind, _ in new_events] == [
        "field_value_edited",
        "field_value_edited",
        "field_value_edited",
        "recap_updated",
        "state_changed",
        "scope_changed",
        "ticket_status_changed",
    ]
    assert [payload["field"] for kind, payload in new_events if kind == "field_value_edited"] == [
        "kickoff",
        "success",
        "approach",
    ]
    assert new_events[4][1] == {
        "from": "needs_success",
        "to": "needs_plan",
        "cause": "external_work",
    }
    assert new_events[5][1] == {
        "ceiling": "needs_plan",
        "at_cap": "stop",
        "cause": "external_work",
    }


def test_create_external_work_emits_exact_existing_events_and_rings(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    class Rings:
        count = 0
        def ring(self) -> None:
            self.count += 1
    rings = Rings()
    app.state.readiness_doorbell = rings
    with TestClient(app) as client:
        response = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "title": "Already done",
                "recap": "done elsewhere",
                **_external_body(TicketState.done),
            },
            headers=_CHIEF,
        )
    assert response.status_code == 200, response.json()
    assert rings.count == 1
    events = _events(db_path, response.json()["id"])
    assert [kind for kind, _ in events] == [
        "ticket_created",
        "field_value_edited",
        "field_value_edited",
        "field_value_edited",
        "field_value_edited",
        "field_value_edited",
        "recap_updated",
        "state_changed",
        "scope_changed",
    ]
    assert [payload["field"] for kind, payload in events if kind == "field_value_edited"] == [
        "success",
        "approach",
        "plan",
        "implementation",
        "closeout",
    ]
    assert events[7][1] == {
        "from": "needs_success",
        "to": "done",
        "cause": "external_work",
    }
    assert events[8][1] == {
        "ceiling": "done",
        "at_cap": "stop",
        "cause": "external_work",
    }


def test_reconcile_safety_reads_happen_after_begin_immediate(tmp_path: Path) -> None:
    _app, db_path = _make_app(tmp_path)
    ticket_id = _ordinary_ticket(db_path)
    lock_holder = connect(str(db_path))
    attempted_begin = threading.Event()
    outcome: queue.Queue[object] = queue.Queue()

    lock_holder.execute("BEGIN IMMEDIATE")
    lock_holder.execute(
        "UPDATE tickets SET ticket_status = 'agent_running_step' WHERE id = ?",
        (ticket_id,),
    )

    def reconcile_after_lock() -> None:
        conn = connect(str(db_path))
        conn.set_trace_callback(
            lambda statement: attempted_begin.set()
            if statement.strip().upper() == "BEGIN IMMEDIATE"
            else None
        )
        try:
            reconcile_ticket_from_external_work(
                conn,
                ticket_id,
                kickoff_note="External report",
                target_state=TicketState.needs_success,
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
            "SELECT state, fields, ticket_status FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        assert ticket["state"] == "needs_success"
        assert json.loads(ticket["fields"])["kickoff"]["value"] == "Original report"
        assert ticket["ticket_status"] == "agent_running_step"
    finally:
        conn.close()


def test_parent_item_events_cover_external_create_state_and_status_changes(tmp_path: Path) -> None:
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
        before = _events(db_path, item.id)
    finally:
        conn.close()

    with TestClient(app) as client:
        created_response = client.post(
            "/api/chief/tickets/from-external-work",
            json={
                "title": "Parented external work",
                "sprint_item_id": item.id,
                **_external_body(TicketState.needs_success),
            },
            headers=_CHIEF,
        )
    assert created_response.status_code == 200, created_response.json()
    ticket_id = created_response.json()["id"]
    after_create = _events(db_path, item.id)[len(before):]
    assert after_create == [
        ("item_children_changed", {"ticket_id": ticket_id, "reason": "created"})
    ]

    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = 'errored' WHERE id = ?", (ticket_id,)
        )
        conn.commit()
    finally:
        conn.close()
    before_reconcile = _events(db_path, item.id)

    with TestClient(app) as client:
        reconciled_response = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body(TicketState.needs_approach),
            headers=_CHIEF,
        )
    assert reconciled_response.status_code == 200, reconciled_response.json()
    assert _events(db_path, item.id)[len(before_reconcile):] == [
        ("item_children_changed", {"ticket_id": ticket_id, "reason": "state"}),
        ("item_children_changed", {"ticket_id": ticket_id, "reason": "ticket_status"}),
    ]
