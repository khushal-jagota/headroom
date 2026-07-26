"""The public New Worker flow: a proposal parks, and a public accept advances it.

This is Ticket machinery over the real HTTP boundary and nothing else. It used to run
inside a scripted agent conversation, which proved something real at the time — that the
worker's automatic opening turn and a person's own turn shared one conversation — but that
is now how a Ticket is built rather than something a test can catch it failing at: a
Ticket names one conversation, and everything that sends into it sends into that one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.days.logic.dates import resolve_day_id
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap

_AGENT = {"X-Plan-Actor": "agent"}


def _application(tmp_path: Path) -> tuple[Any, str]:
    db_path = tmp_path / "new-worker.db"
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
        },
    )
    clock = build_clock(config)
    with connect(str(db_path)) as conn:
        create_schema(conn)
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="new_worker",
            title="Design a public API worker",
            actor="human",
            now=clock.now_unix(),
            title_max_chars=200,
        )
        tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=clock.now_unix(),
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        )
        days_data.add_day_ticket(
            conn,
            resolve_day_id("today", clock.now(), config.boundary_hour),
            ticket.id,
            clock.now_unix(),
        )
    return create_app(config, clock, lambda: connect(str(db_path))), ticket.id


def test_new_worker_understanding_proposal_parks_and_public_accept_advances(
    tmp_path: Path,
) -> None:
    app, ticket_id = _application(tmp_path)
    with TestClient(app) as client:
        opened = client.get(f"/api/tickets/{ticket_id}").json()
        assert opened["stage"] == "needs_understanding"
        assert opened["fields"]["understanding"]["proposal"] is None

        proposed = client.post(
            f"/api/tickets/{ticket_id}/propose",
            headers=_AGENT,
            json={
                "body": "Purpose, judgment risks, and boundaries captured.",
                "recap": "Understanding ready.",
            },
        )
        assert proposed.status_code == 200, proposed.text
        assert proposed.json()["ticket_status"] == "awaiting_approval"

        accepted = client.post(
            f"/api/tickets/{ticket_id}/accept/understanding",
            json={"next_ceiling": "needs_stages", "at_cap": "propose"},
        )

    assert accepted.status_code == 200, accepted.text
    body = accepted.json()
    assert body["stage"] == "needs_stages"
    assert body["fields"]["understanding"]["proposal"] is None
    assert body["fields"]["understanding"]["value"].startswith("Purpose")
