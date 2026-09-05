"""API-level tests for PUT /api/tickets/{id}/value/{field}.

The route admits every actor for pending proposals and preserves the direct-only gate
for settled values. Supporting tests, no §18.3 anchor.
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core import change_signal
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.contracts import NO_FURTHER, AtCap
from planner.tickets.data import (
    accept_proposal,
    change_scope,
    create_ticket,
    file_proposal,
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
        file_proposal(
            conn, ticket.id, field="success", body="success v1", actor="agent", now=0
        )
        file_proposal(
            conn, ticket.id, field="approach", body="approach v1", actor="agent", now=0
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


def test_put_value_agent_edits_pending_proposal_in_place(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    conn = connect(str(db_path))
    try:
        ticket = file_proposal(
            conn,
            tid,
            field="plan",
            body="plan draft",
            actor="original-worker",
            now=23,
        )
        original = ticket.fields.slots["plan"].proposal
        assert original is not None
    finally:
        conn.close()

    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{tid}/value/plan",
            json={"body": "edited plan draft"},
            headers=_AGENT,
        )

    assert response.status_code == 200, response.json()
    body = response.json()
    proposal = body["fields"]["plan"]["proposal"]
    assert proposal == {
        "body": "edited plan draft",
        "proposed_by": original.proposed_by,
        "created_at": original.created_at,
    }
    assert body["fields"]["plan"]["value"] is None
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


def test_put_value_signals_the_change_after_a_successful_edit(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    tid = _passed_ticket(db_path)
    signals = 0

    def record() -> None:
        nonlocal signals
        signals += 1

    unsubscribe = change_signal.subscribe(record)
    try:
        with TestClient(app) as client:
            response = client.put(
                f"/api/tickets/{tid}/value/success", json={"body": "edited success"}
            )
            detail = client.get(f"/api/tickets/{tid}").json()
    finally:
        unsubscribe()

    assert response.status_code == 200, response.json()
    assert detail["fields"]["success"]["value"] == "edited success"
    assert signals == 1
