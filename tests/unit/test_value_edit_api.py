"""API tests for the distinct saved-value and pending-proposal edit routes."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import actions as tickets_actions
from planner.tickets.contracts import NO_FURTHER
from planner.tickets.data import (
    accept_proposal,
    create_ticket,
    file_current_proposal_with_recap,
    set_ceiling,
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
        set_ceiling(
            conn,
            ticket.id,
            ceiling="needs_plan",
            principal=OWNER_PRINCIPAL,
            now=0,
        )
        file_current_proposal_with_recap(
            conn,
            ticket.id,
            body="success v1",
            recap="r",
            principal=ticket_principal(ticket.id),
            now=0,
        )
        file_current_proposal_with_recap(
            conn,
            ticket.id,
            body="approach v1",
            recap="r",
            principal=ticket_principal(ticket.id),
            now=0,
        )
    finally:
        conn.close()
    return ticket.id


def test_put_value_human_edits_settled_field(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{tid}/value/success", json={"body": "edited success"}
        )
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["field_values"]["success"] == "edited success"
    assert body["stage"] == "needs_plan"  # value edit leaves state untouched
    assert body["ceiling"] == "needs_plan"


def test_put_value_completes_current_user_owned_gate_and_advances(
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
        response = client.put(
            f"/api/tickets/{ticket.id}/value/kickoff",
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


def test_put_value_rejects_unset_worker_owned_gate_without_changing_ticket(
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
        response = client.put(
            f"/api/tickets/{ticket.id}/value/kickoff",
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
        tickets_actions.add_link(conn, blocker.id, ticket.id, LinkKind.blocks, now=1)
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{ticket.id}/value/kickoff",
            json={"body": "User context"},
        )

    assert response.status_code == 200, response.json()
    assert response.json()["stage"] == "needs_outcome"
    assert response.json()["ticket_status"] == "blocked"


def test_put_proposal_agent_edits_pending_proposal_in_place(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    conn = connect(str(db_path))
    try:
        ticket = file_current_proposal_with_recap(
            conn,
            tid,
            body="plan draft",
            recap="r",
            principal=ticket_principal(tid),
            now=23,
        )
        original = ticket.pending_proposal
        assert original is not None
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{tid}/proposal",
            json={"field": "plan", "body": "edited plan draft"},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": tid},
        )

    assert response.status_code == 200, response.json()
    body = response.json()
    proposal = body["pending_proposal"]
    assert proposal == {
        "field": "plan",
        "body": "edited plan draft",
        "proposed_by": original.proposed_by,
        "created_at": original.created_at,
    }
    assert "plan" not in body["field_values"]
    assert body["guidance"] == ""
    assert body["stage"] == "needs_plan"
    assert body["ceiling"] == "needs_plan"
    assert body["ticket_status"] == "awaiting_approval"


def test_put_value_bad_field_is_validation_error(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.put(f"/api/tickets/{tid}/value/bogus", json={"body": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
