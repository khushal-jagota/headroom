"""Explicit assignment preserves identity, authority, and global owner attention."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.contracts import Principal, PrincipalKind, Priority
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.projects import data as projects_data
from planner.sprints import data as sprints_data
from planner.tickets import actions, data, views
from planner.tickets.contracts import Ticket
from planner.worker_types.contracts import RETIRED_PLANNING_WORKER_TYPES

CHIEF = Principal(PrincipalKind.chief, "chief")


def _item(conn: Connection) -> str:
    project = projects_data.create_project(conn, name="Item Project", priority=Priority.P2, now=1)
    return sprints_data.create_item(
        conn, title="An orchestrator", project_id=project.id, clock=RealClock()
    ).id


def _ticket(
    conn: Connection,
    *,
    worker_type: str = "coding",
    sprint_item_id: str | None = None,
    stated_holder: Principal | None = None,
) -> Ticket:
    return data.create_ticket(
        conn,
        title="A Worker",
        worker_type=worker_type,
        sprint_item_id=sprint_item_id,
        stated_holder=stated_holder,
        kickoff_note="An agreed brief.",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )


def test_chief_assignment_is_explicit_and_preserves_identity_and_owner_holder(
    tmp_db: Connection,
) -> None:
    item_id = _item(tmp_db)
    ticket = _ticket(tmp_db, sprint_item_id=item_id)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='existing-conversation' WHERE id=?", (ticket.id,)
    )
    moved = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"orchestrator": CHIEF},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=2,
    )
    assert moved.id == ticket.id
    assert moved.conversation_id == "existing-conversation"
    assert moved.orchestrator_kind == "chief"
    assert moved.sprint_item_id is None
    assert moved.ceiling_holder == OWNER_PRINCIPAL
    assert moved.project_id == ticket.project_id
    assert views.ticket_json(moved, 2)["orchestrator"] == {"kind": "chief", "id": "chief"}
    assert (
        tmp_db.execute("SELECT orchestrator_kind FROM tickets WHERE id=?", (ticket.id,)).fetchone()[
            0
        ]
        == "chief"
    )


def test_normal_assignment_inherits_project_and_retains_chief_holder(tmp_db: Connection) -> None:
    ticket = _ticket(tmp_db)
    ticket = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"ceiling_holder": CHIEF},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=2,
    )
    item_id = _item(tmp_db)
    moved = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"orchestrator": Principal(PrincipalKind.sprint_item, item_id)},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=2,
    )
    assert moved.orchestrator_kind == "sprint_item"
    assert moved.sprint_item_id == item_id
    assert moved.ceiling_holder == CHIEF
    assert moved.project_id == sprints_data.read_item(tmp_db, item_id).item.project_id
    assert (
        tmp_db.execute("SELECT project_id FROM tickets WHERE id=?", (ticket.id,)).fetchone()[0]
        is None
    )


def test_a_move_to_chief_releases_only_an_invalid_old_parent_holder(tmp_db: Connection) -> None:
    item_id = _item(tmp_db)
    ticket = _ticket(
        tmp_db,
        sprint_item_id=item_id,
        stated_holder=Principal(PrincipalKind.sprint_item, item_id),
    )
    moved = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"orchestrator": CHIEF},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=2,
    )
    assert moved.ceiling_holder == OWNER_PRINCIPAL
    assert "ceiling returned" in moved.guidance


def test_creation_has_no_calendar_defaults_and_chief_is_a_real_destination(
    tmp_db: Connection,
) -> None:
    ticket = actions.create_ticket(
        tmp_db,
        title="No calendar",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        orchestrator_kind="chief",
        planning_now=datetime(2026, 10, 16, tzinfo=UTC),
    )
    assert ticket.orchestrator_kind == "chief"
    assert ticket.sprint_id is None
    assert tmp_db.execute("SELECT COUNT(*) FROM days").fetchone()[0] == 0
    assert tmp_db.execute("SELECT COUNT(*) FROM day_tickets").fetchone()[0] == 0


@pytest.mark.parametrize("worker_type", sorted(RETIRED_PLANNING_WORKER_TYPES))
def test_all_creation_writers_refuse_retired_planning_workers(
    tmp_db: Connection, worker_type: str
) -> None:
    with pytest.raises(PlannerError, match="retired"):
        _ticket(tmp_db, worker_type=worker_type)
    with pytest.raises(PlannerError, match="retired"):
        data.validate_ticket_creation_context(
            tmp_db, title="Retired", worker_type=worker_type, title_max_chars=200
        )


def test_board_and_review_find_off_day_work_under_a_settled_orchestrator(
    tmp_db: Connection,
) -> None:
    item_id = _item(tmp_db)
    ticket = _ticket(tmp_db, sprint_item_id=item_id)
    tmp_db.execute("UPDATE sprint_items SET settled=1 WHERE id=?", (item_id,))
    board = views.board_view(tmp_db)
    cards = [card for column in board["columns"] for card in column["cards"]]
    assert next(card for card in cards if card["id"] == ticket.id)["has_pending_proposal"] is True
    assert board["sprint_items"][0]["settled"] is True
    assert views.review_view(tmp_db)["items"][0]["ticket_id"] == ticket.id


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = tmp_path / "assignment.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)})
    app = create_app(
        cfg,
        build_clock(cfg),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as test_client:
        yield test_client


def test_api_creates_and_reassigns_chief_without_a_thin_item(client: TestClient) -> None:
    created = client.post(
        "/api/tickets",
        json={
            "title": "Chief child",
            "worker_type": "coding",
            "orchestrator": {"kind": "chief", "id": "chief"},
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["orchestrator"] == {"kind": "chief", "id": "chief"}
    ticket_id = created.json()["id"]
    result = client.patch(
        f"/api/tickets/{ticket_id}", json={"orchestrator": {"kind": "chief", "id": "chief"}}
    )
    assert result.status_code == 200, result.text
    assert result.json()["ceiling_holder"] == {"kind": "owner", "id": "owner"}


@pytest.mark.parametrize("field", ["sprint_id", "sprint_item_id", "day", "day_id"])
def test_api_rejects_calendar_and_legacy_assignment_creation(
    client: TestClient, field: str
) -> None:
    response = client.post(
        "/api/tickets", json={"title": "Old placement", "worker_type": "coding", field: None}
    )
    assert response.status_code == 400


@pytest.mark.parametrize("query", ["day=today", "sprint_id=null"])
def test_api_rejects_calendar_list_filters(client: TestClient, query: str) -> None:
    assert client.get(f"/api/tickets?{query}").status_code == 400
