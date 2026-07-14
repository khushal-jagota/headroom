"""API-level tests for PUT /api/tickets/{id}/value/{field} (Decision B). Exercises
ValueEditBody marshalling, the direct-only gate, bad-field validation,
the ticket_json response shape, and that the edit lands a field_value_edited event
on GET /api/tickets/{id}/events (the _apply_decision appender path). Supporting tests,
no §18.3 anchor.
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.contracts import NO_FURTHER, AtCap, CodingStage, FieldName
from planner.tickets.data import accept_proposal, change_scope, create_ticket, file_proposal

_AGENT = {"X-Plan-Actor": "agent"}  # a plain (non-dispatched) agent context


def _make_app(tmp_path: Path) -> tuple[object, Path]:
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
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


def _passed_ticket(db_path: Path) -> str:
    """Drive a ticket to needs_plan with success & approach settled (values)."""
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn, worker_type="coding", title="Edit me.", actor="human", now=0, title_max_chars=200
        )
        ticket = accept_proposal(
            conn,
            ticket.id,
            field=FieldName.kickoff,
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        change_scope(
            conn,
            ticket.id,
            ceiling=CodingStage.needs_plan,
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_proposal(
            conn, ticket.id, field=FieldName.success, body="success v1", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field=FieldName.approach, body="approach v1", actor="agent", now=0
        )
    finally:
        conn.close()
    return ticket.id


def test_put_value_human_edits_settled_field(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.put(f"/api/tickets/{tid}/value/success", json={"body": "edited success"})
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["fields"]["success"]["value"] == "edited success"
    assert body["stage"] == "needs_plan"  # value edit leaves state untouched
    assert body["ceiling"] == "needs_plan"


def test_put_value_agent_is_forbidden(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{tid}/value/success", json={"body": "x"}, headers=_AGENT
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_put_value_bad_field_is_validation_error(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.put(f"/api/tickets/{tid}/value/bogus", json={"body": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_put_value_logs_field_value_edited_event(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        edit = client.put(f"/api/tickets/{tid}/value/success", json={"body": "edited success"})
        assert edit.status_code == 200, edit.json()
        events = client.get(f"/api/tickets/{tid}/events").json()["events"]
    matching = [e for e in events if e["kind"] == "field_value_edited"]
    assert len(matching) == 1
    assert matching[0]["payload"] == {"field": "success", "body": "edited success"}


def test_put_value_rings_readiness_doorbell_after_successful_edit(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)

    class RecordingDoorbell:
        def __init__(self) -> None:
            self.rings = 0

        def ring(self) -> None:
            self.rings += 1

    doorbell = RecordingDoorbell()
    app.state.readiness_doorbell = doorbell

    with TestClient(app) as client:
        response = client.put(f"/api/tickets/{tid}/value/success", json={"body": "edited success"})

    assert response.status_code == 200, response.json()
    assert doorbell.rings == 1
