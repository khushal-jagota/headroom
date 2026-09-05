"""API tests for the distinct saved-value and pending-proposal edit routes."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.contracts import NO_FURTHER, AtCap
from planner.tickets.data import (
    accept_proposal,
    change_scope,
    create_ticket,
    file_current_proposal_with_recap,
)

_AGENT = {"X-Plan-Actor": "agent"}  # a plain (non-dispatched) agent context


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
            actor="human",
            now=0,
            title_max_chars=200,
        )
        ticket = accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=0,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        change_scope(
            conn,
            ticket.id,
            ceiling="needs_plan",
            at_cap=AtCap.propose,
            actor="human",
            now=0,
        )
        file_current_proposal_with_recap(
            conn, ticket.id, body="success v1", recap="r", actor="agent", now=0
        )
        file_current_proposal_with_recap(
            conn, ticket.id, body="approach v1", recap="r", actor="agent", now=0
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
    assert body["field_values"]["success"] == "edited success"
    assert body["stage"] == "needs_plan"  # value edit leaves state untouched
    assert body["ceiling"] == "needs_plan"


def test_put_proposal_agent_edits_pending_proposal_in_place(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    conn = connect(str(db_path))
    try:
        ticket = file_current_proposal_with_recap(
            conn, tid, body="plan draft", recap="r", actor="original-worker", now=23
        )
        original = ticket.pending_proposal
        assert original is not None
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{tid}/proposal",
            json={"field": "plan", "body": "edited plan draft"},
            headers=_AGENT,
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
    assert body["at_cap"] == "propose"
    assert body["ticket_status"] == "awaiting_approval"


def test_put_value_bad_field_is_validation_error(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    with TestClient(app) as client:
        response = client.put(f"/api/tickets/{tid}/value/bogus", json={"body": "x"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"
