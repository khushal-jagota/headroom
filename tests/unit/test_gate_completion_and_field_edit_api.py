"""API tests for completing a user-owned gate and for editing a settled value."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import actions as tickets_actions
from planner.tickets.contracts import NO_FURTHER, TicketEdit
from planner.tickets.data import (
    accept_proposal,
    create_ticket,
    edit_ticket,
    file_current_proposal,
)


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
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path


def _passed_ticket(db_path: Path) -> str:
    """Drive a ticket to needs_plan with success & approach settled (values)."""
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Edit me.",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
        )
        ticket = accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            principal=OWNER_PRINCIPAL,
            now=0,
            next_ceiling=NO_FURTHER,
            next_holder=OWNER_PRINCIPAL,
        )
        edit_ticket(
            conn,
            ticket.id,
            edit=TicketEdit(ceiling="needs_plan"),
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=0,
        )
        file_current_proposal(
            conn,
            ticket.id,
            body="success v1",
            principal=ticket_principal(ticket.id),
            now=0,
        )
        file_current_proposal(
            conn,
            ticket.id,
            body="approach v1",
            principal=ticket_principal(ticket.id),
            now=0,
        )
    finally:
        conn.close()
    return ticket.id


def test_the_one_edit_corrects_a_settled_field(tmp_path: Path) -> None:
    """A settled value is a Ticket field, so it changes the way every field changes."""
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.patch(
            f"/api/tickets/{tid}", json={"field_values": {"success": "edited success"}}
        )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["field_values"]["success"] == "edited success"
    assert body["stage"] == "needs_plan"  # the correction moves nothing
    assert body["ceiling"] == "needs_plan"


def test_the_one_edit_refuses_to_fill_a_blank(tmp_path: Path) -> None:
    """Filling the current gate is completion, an operation, and not this door."""
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.patch(f"/api/tickets/{tid}", json={"field_values": {"plan": "draft"}})
        after = client.get(f"/api/tickets/{tid}").json()
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
    assert "plan" not in after["field_values"]


def test_completing_a_user_owned_gate_advances_the_ticket(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="personal",
            title="Personal task",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
            kickoff_note=None,
        )
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket.id}/complete/kickoff",
            json={"body": "User context"},
        )

    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["field_values"] == {"kickoff": "User context"}
    assert body["stage"] == "needs_outcome"
    assert body["ceiling"] == "needs_outcome"
    assert body["ceiling_holder"] == {"kind": "owner", "id": "owner"}
    assert body["ticket_status"] == "empty"
    assert body["pending_proposal"] is None


def test_completion_rejects_a_worker_owned_gate_without_changing_the_ticket(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Worker task",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
            kickoff_note=None,
        )
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket.id}/complete/kickoff",
            json={"body": "Not allowed"},
        )
        after = client.get(f"/api/tickets/{ticket.id}").json()

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"
    assert after["stage"] == ticket.stage
    assert after["field_values"] == {}


def test_user_completion_enters_blocked_when_a_live_blocker_exists(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    try:
        blocker = create_ticket(
            conn,
            worker_type="coding",
            title="Blocker",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
            kickoff_note=None,
        )
        ticket = create_ticket(
            conn,
            worker_type="personal",
            title="Blocked task",
            principal=OWNER_PRINCIPAL,
            now=0,
            title_max_chars=200,
            kickoff_note=None,
        )
        tickets_actions.add_ticket_block(conn, blocker.id, ticket.id, now=1)
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket.id}/complete/kickoff",
            json={"body": "User context"},
        )

    assert response.status_code == 200, response.json()
    assert response.json()["stage"] == "needs_outcome"
    assert response.json()["ticket_status"] == "blocked"


def test_a_parked_proposal_has_no_edit_door(tmp_path: Path) -> None:
    """Approve it, or send it back. There is no third thing to do with a proposal."""
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    conn = connect(str(db_path))
    try:
        ticket = file_current_proposal(
            conn,
            tid,
            body="plan draft",
            principal=ticket_principal(tid),
            now=23,
        )
        assert ticket.pending_proposal is not None
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{tid}/proposal",
            json={"field": "plan", "body": "edited plan draft"},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": tid},
        )
        after = client.get(f"/api/tickets/{tid}").json()

    assert response.status_code == 404
    assert after["pending_proposal"]["body"] == "plan draft"


def test_completing_an_unknown_field_is_a_validation_error(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.post(f"/api/tickets/{tid}/complete/bogus", json={"body": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
