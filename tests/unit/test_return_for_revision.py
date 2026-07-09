from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.adapters.registry import build_adapters
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.contracts import AtCap, FieldName, TicketState
from planner.tickets.data import (
    change_scope,
    create_ticket,
    file_proposal,
    finish_run_if_still_running_step,
)

_AGENT = {"X-Plan-Actor": "agent"}
_PREFIX = "The user rejected your proposal and provided the following guidance:"


class FakeSystemA:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_to_claimed_worker(self, ticket_id: str, message: str) -> None:
        self.sent.append((ticket_id, message))


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
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


def _ticket_with_pending_plan(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(conn, title="Revise plan", actor="human", now=0, title_max_chars=200)
        change_scope(
            conn,
            ticket.id,
            ceiling=TicketState.needs_plan,
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_proposal(
            conn, ticket.id, field=FieldName.success, body="success", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field=FieldName.approach, body="approach", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field=FieldName.plan, body="bad plan", actor="agent", now=0
        )
        finish_run_if_still_running_step(
            conn, ticket.id, session_key=f"session-{ticket.id}", now=0
        )
    finally:
        conn.close()
    return ticket.id


def _ticket_needs_review(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn, title="Revise result", actor="human", now=0, title_max_chars=200
        )
        change_scope(
            conn,
            ticket.id,
            ceiling=TicketState.needs_review,
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_proposal(
            conn, ticket.id, field=FieldName.success, body="success", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field=FieldName.approach, body="approach", actor="agent", now=0
        )
        file_proposal(conn, ticket.id, field=FieldName.plan, body="plan", actor="agent", now=0)
        file_proposal(
            conn, ticket.id, field=FieldName.result, body="bad result", actor="agent", now=0
        )
        finish_run_if_still_running_step(
            conn, ticket.id, session_key=f"session-{ticket.id}", now=0
        )
    finally:
        conn.close()
    return ticket.id


def test_return_for_revision_clears_proposal_and_sends_direct_worker_message(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    fake_system_a = FakeSystemA()
    app.state.system_a = fake_system_a

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "Make it shorter."},
        )
        assert response.status_code == 200, response.json()
        ticket = response.json()
        chat = client.get(f"/api/chat/{tid}/state").json()
        events = client.get(f"/api/tickets/{tid}/events").json()["events"]
        approvals = client.get("/api/queues").json()["approvals"]
        duplicate = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "Duplicate send."},
        )

    assert ticket["state"] == "needs_plan"
    assert ticket["ticket_status"] == "agent_running_step"
    assert ticket["fields"]["plan"]["value"] is None
    assert ticket["fields"]["plan"]["proposal"] is None
    assert chat["messages"] == []
    assert all(event["kind"] != "approval_returned" for event in events)
    assert fake_system_a.sent == [(tid, f"{_PREFIX}\n\nMake it shorter.")]
    assert approvals == []
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "already_running"


def test_return_for_revision_keeps_final_review_stage_and_sends_worker_message(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_needs_review(db_path)
    fake_system_a = FakeSystemA()
    app.state.system_a = fake_system_a

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "The result needs evidence."},
        )
        assert response.status_code == 200, response.json()
        ticket = response.json()
        events = client.get(f"/api/tickets/{tid}/events").json()["events"]
        stale_approve = client.post(f"/api/tickets/{tid}/approve")

    assert ticket["state"] == "needs_review"
    assert ticket["ticket_status"] == "agent_running_step"
    assert ticket["fields"]["result"]["value"] == "bad result"
    assert ticket["fields"]["result"]["proposal"] is None
    assert all(event["kind"] != "approval_returned" for event in events)
    assert all(
        event["payload"].get("cause") != "return_for_revision"
        for event in events
        if event["kind"] == "state_changed"
    )
    assert fake_system_a.sent == [(tid, f"{_PREFIX}\n\nThe result needs evidence.")]
    assert stale_approve.status_code == 409
    assert stale_approve.json()["error"]["code"] == "already_running"
    with TestClient(app) as client:
        assert client.get("/api/queues").json()["approvals"] == []


def test_return_for_revision_requires_existing_worker_session(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)
    conn = connect(str(db_path))
    try:
        conn.execute("UPDATE tickets SET chat_session_key = NULL WHERE id = ?", (tid,))
        conn.commit()
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "Revise it."},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_return_for_revision_agent_forbidden_and_requires_message(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _ticket_with_pending_plan(db_path)

    with TestClient(app) as client:
        agent = client.post(
            f"/api/tickets/{tid}/return-for-revision",
            json={"message": "x"},
            headers=_AGENT,
        )
        empty = client.post(f"/api/tickets/{tid}/return-for-revision", json={"message": ""})

    assert agent.status_code == 400
    assert agent.json()["error"]["code"] == "agent_forbidden"
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "validation"
