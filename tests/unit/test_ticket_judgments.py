"""Storage, authority, projection, and migration coverage for Ticket verdicts."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection, IntegrityError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core import change_signal
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.judgments import data as judgments_data
from planner.judgments.contracts import TicketJudgment
from planner.judgments.logic.verdicts import normalize_verdict
from planner.tickets import data as tickets_data

_AGENT = {"X-Plan-Actor": "worker"}


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "planning-test.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, conn_factory), db_path


def _ticket(db_path: Path, *, stage: str) -> str:
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Judge this Ticket",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        conn.execute("UPDATE tickets SET stage = ? WHERE id = ?", (stage, ticket.id))
        return ticket.id
    finally:
        conn.close()


def test_fresh_schema_has_constrained_cascading_judgment_record(tmp_path: Path) -> None:
    db_path = tmp_path / "judgments.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket_id = _ticket(db_path, stage="done")

    judgments_data.upsert_verdict(
        conn, ticket_id, rating=5, text="  Exactly right.  "
    )
    assert judgments_data.read_ticket_judgment(conn, ticket_id) == (
        TicketJudgment(
            ticket_id=ticket_id,
            verdict_rating=5,
            verdict_text="Exactly right.",
        )
    )
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        conn.execute(
            "INSERT INTO ticket_judgments (ticket_id, verdict_rating) VALUES (?, 6) "
            "ON CONFLICT(ticket_id) DO UPDATE SET verdict_rating = 6",
            (ticket_id,),
        )
    conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    assert judgments_data.read_ticket_judgment(conn, ticket_id) is None
    conn.close()


def test_verdict_writer_requires_done_and_preserves_an_empty_record(tmp_path: Path) -> None:
    db_path = tmp_path / "writer.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket_id = _ticket(db_path, stage="needs_closeout")

    with pytest.raises(PlannerError, match="only when the ticket is done"):
        judgments_data.upsert_verdict(conn, ticket_id, rating=3, text=None)
    conn.execute("UPDATE tickets SET stage = 'done' WHERE id = ?", (ticket_id,))

    assert judgments_data.upsert_verdict(conn, ticket_id, rating=3, text=None) is not None
    assert judgments_data.upsert_verdict(conn, ticket_id, rating=None, text="Useful") is not None
    assert judgments_data.upsert_verdict(conn, ticket_id, rating=None, text="  ") is None
    assert judgments_data.read_verdict(conn, ticket_id) is None
    assert judgments_data.read_ticket_judgment(conn, ticket_id) is not None
    conn.close()


def test_domain_normalization_rejects_float_rating() -> None:
    with pytest.raises(PlannerError) as raised:
        normalize_verdict(2.5, None)
    assert raised.value.code == ErrorCode.validation


def test_domain_normalization_rejects_non_string_text() -> None:
    with pytest.raises(PlannerError) as raised:
        normalize_verdict(None, 42)
    assert raised.value.code == ErrorCode.validation


def test_verdict_api_projects_optional_fields_and_clear(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path, stage="done")

    with TestClient(app) as client:
        assert client.get(f"/api/tickets/{ticket_id}").json()["verdict"] is None

        rating_only = client.put(
            f"/api/tickets/{ticket_id}/verdict", json={"rating": 1, "text": None}
        )
        assert rating_only.status_code == 200, rating_only.json()
        assert rating_only.json() == {"verdict": {"rating": 1, "text": None}}

        text_only = client.put(
            f"/api/tickets/{ticket_id}/verdict",
            json={"rating": None, "text": "  Better than expected.  "},
        )
        assert text_only.json() == {
            "verdict": {"rating": None, "text": "Better than expected."}
        }
        assert client.get(f"/api/tickets/{ticket_id}").json()["verdict"] == (
            {"rating": None, "text": "Better than expected."}
        )

        cleared = client.put(
            f"/api/tickets/{ticket_id}/verdict", json={"rating": None, "text": None}
        )
        assert cleared.json() == {"verdict": None}
        assert client.get(f"/api/tickets/{ticket_id}").json()["verdict"] is None


@pytest.mark.parametrize("rating", [0, 6, True, 2.5, "5"])
def test_verdict_api_rejects_invalid_ratings(tmp_path: Path, rating: object) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path, stage="done")
    with TestClient(app) as client:
        response = client.put(
            f"/api/tickets/{ticket_id}/verdict", json={"rating": rating, "text": None}
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation"


def test_verdict_api_rejects_worker_non_done_and_unknown_ticket(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    done_ticket_id = _ticket(db_path, stage="done")
    open_ticket_id = _ticket(db_path, stage="needs_closeout")
    with TestClient(app) as client:
        worker = client.put(
            f"/api/tickets/{done_ticket_id}/verdict",
            json={"rating": 4, "text": None},
            headers=_AGENT,
        )
        open_ticket = client.put(
            f"/api/tickets/{open_ticket_id}/verdict",
            json={"rating": 4, "text": None},
        )
        unknown = client.put(
            "/api/tickets/t_missing/verdict", json={"rating": 4, "text": None}
        )
    assert worker.json()["error"]["code"] == "agent_forbidden"
    assert open_ticket.json()["error"]["code"] == "validation"
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "not_found"


def test_successful_verdict_write_emits_one_change_signal(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path, stage="done")
    signals = 0

    def record() -> None:
        nonlocal signals
        signals += 1

    unsubscribe = change_signal.subscribe(record)
    try:
        with TestClient(app) as client:
            response = client.put(
                f"/api/tickets/{ticket_id}/verdict",
                json={"rating": 4, "text": "Good work."},
            )
    finally:
        unsubscribe()
    assert response.status_code == 200
    assert signals == 1
