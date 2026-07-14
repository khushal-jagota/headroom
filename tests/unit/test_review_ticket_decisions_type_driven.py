"""Ticket-only Review contract and deletion locks."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.probe import (
    FIELD_ALPHA,
    NEEDS_ALPHA,
    install_probe_registry,
    uninstall_probe_registry,
)

from planner.core.adapters.registry import build_adapters
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.sprints import data as sprints_data
from planner.tickets.contracts import AtCap
from planner.tickets.data import accept_proposal, create_ticket, file_proposal
from planner.tickets.views import review_view
from planner.worker_types.contracts import WorkerTypeDefinition

TODAY_DAY_ID = "day_2026-07-04"
YESTERDAY_DAY_ID = "day_2026-07-03"
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def probe_registry() -> Iterator[WorkerTypeDefinition]:
    definition = install_probe_registry()
    try:
        yield definition
    finally:
        uninstall_probe_registry()


def _review(conn: Connection) -> dict:
    return review_view(conn, day_id=TODAY_DAY_ID)


def _park_after_kickoff(
    conn: Connection,
    *,
    worker_type: str,
    title: str,
    next_ceiling: str,
    field: str,
    proposal_at: int,
) -> str:
    ticket = create_ticket(
        conn,
        worker_type=worker_type,
        title=title,
        actor="human",
        now=1,
        title_max_chars=200,
    )
    accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=2,
        next_ceiling=next_ceiling,
        at_cap=AtCap.propose,
    )
    file_proposal(
        conn,
        ticket.id,
        field=field,
        body=f"{field} proposal",
        actor="agent",
        now=proposal_at,
    )
    return ticket.id


def test_review_uses_each_shipped_worker_types_stored_stage_field(tmp_db: Connection) -> None:
    coding_id = _park_after_kickoff(
        tmp_db,
        worker_type="coding",
        title="Coding",
        next_ceiling="needs_success",
        field="success",
        proposal_at=4,
    )
    new_worker_id = _park_after_kickoff(
        tmp_db,
        worker_type="new_worker",
        title="New worker",
        next_ceiling="needs_stages",
        field="stages",
        proposal_at=3,
    )
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, coding_id, 5)
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, new_worker_id, 5)

    response = _review(tmp_db)

    assert set(response) == {"ticket_decisions", "running_worker_count"}
    assert response["ticket_decisions"] == [
        {
            "ticket_id": new_worker_id,
            "field": "stages",
            "title": "New worker",
            "waiting_since": 3,
        },
        {
            "ticket_id": coding_id,
            "field": "success",
            "title": "Coding",
            "waiting_since": 4,
        },
    ]
    assert all(
        set(decision) == {"ticket_id", "field", "title", "waiting_since"}
        for decision in response["ticket_decisions"]
    )
    assert response["running_worker_count"] == 0


def test_review_uses_test_only_worker_type_field(
    tmp_db: Connection, probe_registry: WorkerTypeDefinition
) -> None:
    probe_id = _park_after_kickoff(
        tmp_db,
        worker_type="probe",
        title="Probe",
        next_ceiling=NEEDS_ALPHA,
        field=FIELD_ALPHA,
        proposal_at=3,
    )
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, probe_id, 4)

    assert _review(tmp_db)["ticket_decisions"] == [
        {
            "ticket_id": probe_id,
            "field": FIELD_ALPHA,
            "title": "Probe",
            "waiting_since": 3,
        }
    ]


def test_review_is_day_scoped_and_orders_equal_times_by_ticket_id(tmp_db: Connection) -> None:
    first_id = _park_after_kickoff(
        tmp_db,
        worker_type="coding",
        title="First equal-time ticket",
        next_ceiling="needs_success",
        field="success",
        proposal_at=5,
    )
    second_id = _park_after_kickoff(
        tmp_db,
        worker_type="coding",
        title="Second equal-time ticket",
        next_ceiling="needs_success",
        field="success",
        proposal_at=5,
    )
    off_day_id = _park_after_kickoff(
        tmp_db,
        worker_type="coding",
        title="Off day",
        next_ceiling="needs_success",
        field="success",
        proposal_at=2,
    )
    no_day_id = _park_after_kickoff(
        tmp_db,
        worker_type="coding",
        title="No day",
        next_ceiling="needs_success",
        field="success",
        proposal_at=1,
    )
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, first_id, 6)
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, second_id, 6)
    days_data.add_day_ticket(tmp_db, YESTERDAY_DAY_ID, off_day_id, 6)

    decision_ids = [row["ticket_id"] for row in _review(tmp_db)["ticket_decisions"]]

    assert decision_ids == sorted((first_id, second_id))
    assert off_day_id not in decision_ids
    assert no_day_id not in decision_ids


def test_running_ticket_is_not_a_decision_and_global_count_includes_off_day(
    tmp_db: Connection,
) -> None:
    today_running_id = _park_after_kickoff(
        tmp_db,
        worker_type="coding",
        title="Running today",
        next_ceiling="needs_success",
        field="success",
        proposal_at=3,
    )
    off_day_running = create_ticket(
        tmp_db,
        worker_type="coding",
        title="Running elsewhere",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    days_data.add_day_ticket(tmp_db, TODAY_DAY_ID, today_running_id, 4)
    days_data.add_day_ticket(tmp_db, YESTERDAY_DAY_ID, off_day_running.id, 4)
    tmp_db.execute(
        "UPDATE tickets SET ticket_status = 'agent_running_step' WHERE id IN (?, ?)",
        (today_running_id, off_day_running.id),
    )

    response = _review(tmp_db)

    assert response["ticket_decisions"] == []
    assert response["running_worker_count"] == 2


def test_overdue_ticket_and_sprint_item_do_not_create_review_output(
    tmp_db: Connection, fake_clock: PlannerTestClock
) -> None:
    ticket = create_ticket(
        tmp_db,
        worker_type="coding",
        title="Past due ticket",
        actor="human",
        now=1,
        title_max_chars=200,
    )
    item = sprints_data.create_item(
        tmp_db,
        title="Past due item",
        project_id="project_vylo",
        clock=fake_clock,
    )
    tmp_db.execute("UPDATE tickets SET deadline = '2020-01-01' WHERE id = ?", (ticket.id,))
    tmp_db.execute("UPDATE sprint_items SET deadline = '2020-01-01' WHERE id = ?", (item.id,))

    assert _review(tmp_db) == {"ticket_decisions": [], "running_worker_count": 0}


def test_review_http_contract_and_old_route_absence(tmp_path: Path) -> None:
    db_path = tmp_path / "review-api.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    app = create_app(
        config,
        build_clock(config),
        build_adapters(config),
        lambda: connect(str(db_path)),
    )

    with TestClient(app) as client:
        review = client.get("/api/review")
        old_route = client.get("/api/queues")

    assert review.status_code == 200
    assert review.json() == {"ticket_decisions": [], "running_worker_count": 0}
    assert old_route.status_code == 404


def test_old_queue_surface_is_absent_from_scoped_live_files() -> None:
    scoped_paths = (
        "src/planner/tickets/views.py",
        "src/planner/tickets/api.py",
        "src/planner/sprints/views.py",
        "web/src/lib/types.ts",
        "web/src/routes/ReviewRoute.svelte",
        "web/src/App.svelte",
        "assets/app.css",
    )
    joined = "\n".join((REPO_ROOT / path).read_text(encoding="utf-8") for path in scoped_paths)
    for forbidden in (
        "/api/queues",
        "queues_view",
        "approval_item_rows",
        "overdue_item_rows",
        "QueueEntry",
        "QueuesResponse",
        "refreshQueuesAfter",
        'resource<QueuesResponse>("queues"',
        '"queues"',
        '"approvals"',
        '"overdue"',
        '"running_agents"',
        ".review-queue-line",
    ):
        assert forbidden not in joined


def test_review_component_has_only_ticket_specific_entry_contract() -> None:
    source = (REPO_ROOT / "web/src/routes/ReviewRoute.svelte").read_text(encoding="utf-8")
    for forbidden in (
        "entity_id",
        "entity_type",
        "entry.kind",
        "/api/items/",
        "data-entity-id",
        "data-kind",
        ".approvals",
        ".overdue",
        ".running_agents",
    ):
        assert forbidden not in source
    assert "data-ticket-id={decision.ticket_id}" in source
    assert "data-field={decision.field}" in source

    accept_source = source.split("function accept(", 1)[1].split("function saveTitle", 1)[0]
    title_source = source.split("function saveTitle", 1)[1].split(
        "async function returnForRevision", 1
    )[0]
    revision_source = source.split("async function returnForRevision", 1)[1].split(
        "onDestroy", 1
    )[0]
    assert source.count("await review.refresh()") == 1
    assert "refreshReviewAfter(mutateJson(" in accept_source
    assert '"review"' not in accept_source
    assert '"review"' in title_source
    assert "refreshReviewAfter(mutateJson(" in revision_source
    assert '"review"' not in revision_source
