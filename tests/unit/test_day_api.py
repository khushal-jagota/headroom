"""Day route wake regressions."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets.data import create_ticket


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


def _ticket(db_path: Path) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type="coding",
            title="Run me today.",
            actor="human",
            now=0,
            title_max_chars=200,
        )
        return ticket.id
    finally:
        conn.close()


def _planning_ticket(db_path: Path, worker_type: str) -> str:
    conn = connect(str(db_path))
    try:
        ticket = create_ticket(
            conn,
            worker_type=worker_type,
            title=f"{worker_type} fixture",
            actor="human",
            now=0,
            title_max_chars=200,
        )
    finally:
        conn.close()
    return ticket.id


def test_day_add_and_removal_are_idempotent_and_land_on_the_day(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        response = client.post("/api/day/today/tickets", json={"ticket_id": ticket_id})
        duplicate = client.post("/api/day/today/tickets", json={"ticket_id": ticket_id})
        removed = client.delete(f"/api/day/today/tickets/{ticket_id}")
        absent = client.delete(f"/api/day/today/tickets/{ticket_id}")

    assert response.status_code == 200, response.json()
    assert duplicate.status_code == 200, duplicate.json()
    assert removed.status_code == 200, removed.json()
    assert absent.status_code == 200, absent.json()
    assert response.json()["tickets"][0]["id"] == ticket_id
    assert duplicate.json()["tickets"][0]["id"] == ticket_id
    assert removed.json()["tickets"] == []
    assert absent.json()["tickets"] == []


def test_day_ticket_view_includes_board_status_and_conversation_signals(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    ticket_id = _ticket(db_path)

    with TestClient(app) as client:
        added = client.post("/api/day/today/tickets", json={"ticket_id": ticket_id})

    assert added.status_code == 200, added.json()
    ticket = added.json()["tickets"][0]
    assert ticket["id"] == ticket_id
    assert ticket["stage"] == "needs_kickoff"
    assert ticket["ticket_status"] in {"empty", "awaiting_approval"}
    assert ticket["is_done"] is False
    assert ticket["waiting_to_closeout"] is False
    assert ticket["conversation_id"] is None
    assert ticket["agent_working"] is False
    assert ticket["needs_me"] is False
    assert ticket["latest_turn_ended_sequence"] == 0


def test_day_planning_workers_receive_only_their_exact_fields(
    tmp_path: Path,
    planning_worker_registry: None,
) -> None:
    app, db_path = _make_app(tmp_path)
    day_ticket = _planning_ticket(db_path, "planning-day")
    midday_ticket = _planning_ticket(db_path, "planning-midday-check")

    def headers(ticket_id: str) -> dict[str, str]:
        return {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id}

    with TestClient(app) as client:
        morning = client.patch(
            "/api/day/2026-07-28",
            json={"focus": "Ship it", "brief_take": "The morning plan."},
            headers=headers(day_ticket),
        )
        midday = client.patch(
            "/api/day/2026-07-28",
            json={"midday_reconciliation": "One task moved; the bet still holds."},
            headers=headers(midday_ticket),
        )
        day_cannot_reconcile = client.patch(
            "/api/day/2026-07-28",
            json={"midday_reconciliation": "Wrong worker."},
            headers=headers(day_ticket),
        )
        midday_cannot_rewrite_morning = client.patch(
            "/api/day/2026-07-28",
            json={"focus": "Rewrite"},
            headers=headers(midday_ticket),
        )
        midday_cannot_write_notes = client.patch(
            "/api/day/2026-07-28",
            json={"notes": "Out of scope"},
            headers=headers(midday_ticket),
        )

    assert morning.status_code == 200, morning.json()
    assert midday.status_code == 200, midday.json()
    assert midday.json()["focus"] == "Ship it"
    assert midday.json()["midday_reconciliation"] == "One task moved; the bet still holds."
    for denied in (
        day_cannot_reconcile,
        midday_cannot_rewrite_morning,
        midday_cannot_write_notes,
    ):
        assert denied.status_code == 400
        assert denied.json()["error"]["code"] == "agent_forbidden"


def test_invalid_compound_day_patch_changes_nothing(tmp_path: Path) -> None:
    app, _db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        seeded = client.patch(
            "/api/day/2026-07-28",
            json={"focus": "Original", "watchout": "Original risk"},
        )
        invalid = client.patch(
            "/api/day/2026-07-28",
            json={"focus": "Must roll back", "watchout": 42},
        )
        readback = client.get("/api/day/2026-07-28")

    assert seeded.status_code == 200
    assert invalid.status_code == 400
    assert readback.json()["focus"] == "Original"
    assert readback.json()["watchout"] == "Original risk"
