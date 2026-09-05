"""Storage, authority, projection, and migration coverage for Ticket verdicts."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection, IntegrityError

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.judgments import data as judgments_data
from planner.judgments.contracts import TicketJudgment, TroubleNote
from planner.tickets import data as tickets_data

_AGENT = {"X-Plan-Actor": "worker"}


def _worker(ticket_id: str) -> dict[str, str]:
    return {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id}


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


def _set_ticket_status(db_path: Path, ticket_id: str, ticket_status: str) -> None:
    conn = connect(str(db_path))
    try:
        conn.execute(
            "UPDATE tickets SET ticket_status = ? WHERE id = ?",
            (ticket_status, ticket_id),
        )
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


def test_trouble_note_writer_creates_parent_and_preserves_append_order(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "trouble-writer.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket_id = _ticket(db_path, stage="needs_implementation")
    _set_ticket_status(db_path, ticket_id, "agent")

    first = judgments_data.append_trouble_note(
        conn, ticket_id, body="  Harness lost the process.\n", created_at=20
    )
    second = judgments_data.append_trouble_note(
        conn, ticket_id, body="Tool returned no output.", created_at=10
    )

    assert first == TroubleNote(
        sequence=1, body="Harness lost the process.", created_at=20
    )
    assert second.sequence == 2
    assert judgments_data.read_trouble_notes(conn, ticket_id) == (first, second)
    judgment = judgments_data.read_ticket_judgment(conn, ticket_id)
    assert judgment is not None
    assert judgment.trouble_notes == (first, second)

    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        conn.execute(
            "INSERT INTO ticket_judgment_trouble_notes "
            "(ticket_id, sequence, body, created_at) VALUES (?, 2, 'duplicate', 30)",
            (ticket_id,),
        )

    conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
    assert judgments_data.read_trouble_notes(conn, ticket_id) == ()
    conn.close()


def test_trouble_note_api_requires_exact_current_worker_and_projects_notes(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path, stage="needs_implementation")
    other_ticket_id = _ticket(db_path, stage="needs_implementation")
    _set_ticket_status(db_path, ticket_id, "agent")

    with TestClient(app) as client:
        direct = client.post(
            f"/api/tickets/{ticket_id}/trouble-notes",
            json={"body": "Direct caller"},
        )
        missing_claim = client.post(
            f"/api/tickets/{ticket_id}/trouble-notes",
            json={"body": "Missing claim"},
            headers=_AGENT,
        )
        cross_ticket = client.post(
            f"/api/tickets/{ticket_id}/trouble-notes",
            json={"body": "Wrong Ticket"},
            headers=_worker(other_ticket_id),
        )
        first = client.post(
            f"/api/tickets/{ticket_id}/trouble-notes",
            json={"body": "  Harness dropped the output.  "},
            headers=_worker(ticket_id),
        )
        second = client.post(
            f"/api/tickets/{ticket_id}/trouble-notes",
            json={"body": "The tool timed out."},
            headers=_worker(ticket_id),
        )
        detail = client.get(f"/api/tickets/{ticket_id}").json()

    assert direct.json()["error"]["code"] == "agent_forbidden"
    assert missing_claim.json()["error"]["code"] == "agent_forbidden"
    assert cross_ticket.json()["error"]["code"] == "agent_forbidden"
    assert first.status_code == 200, first.json()
    assert second.status_code == 200, second.json()
    assert first.json()["trouble_note"]["body"] == "Harness dropped the output."
    assert [note["sequence"] for note in detail["trouble_notes"]] == [1, 2]
    assert [note["body"] for note in detail["trouble_notes"]] == [
        "Harness dropped the output.",
        "The tool timed out.",
    ]
    assert all(note["created_at"] > 0 for note in detail["trouble_notes"])


