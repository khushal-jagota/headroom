"""Scoped discovery retains quiet, deferred, completed, and retired children."""

from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_ticket_edit_api import _make_app

from planner.core.contracts import CHIEF_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.list_reads.contracts import ListPageRequest
from planner.sprints import views as sprint_views
from planner.tickets import data, views
from planner.tickets.contracts import TicketListFilters


def seed(
    conn: Connection,
    *,
    title: str,
    item: str | None = None,
    backlogged: bool = False,
    stage: str = "needs_brief",
) -> str:
    ticket = data.create_ticket(
        conn,
        title=title,
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        sprint_item_id=item,
    )
    conn.execute(
        "UPDATE tickets SET stage=?, is_backlogged=? WHERE id=?",
        (stage, int(backlogged), ticket.id),
    )
    return ticket.id


def seed_item(conn: Connection, item: str) -> None:
    conn.execute(
        "INSERT INTO sprint_items(id,title,project_id,supervisor_backend,created_at,updated_at) "
        "VALUES (?,?,'project_vylo','claude',1,1)",
        (item, item),
    )


def test_scope_precedes_counts_and_pagination_and_does_not_change_authority(
    tmp_db: Connection,
) -> None:
    seed_item(tmp_db, "item_one")
    seed_item(tmp_db, "item_two")
    other = seed(tmp_db, title="Other", item="item_two")
    owned = [seed(tmp_db, title="Quiet"), seed(tmp_db, title="Deferred", backlogged=True)]
    retired = seed(tmp_db, title="Retired", stage="cancelled")
    done = seed(tmp_db, title="Done", stage="done")
    item = seed(tmp_db, title="Item child", item="item_one", backlogged=True)
    before = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (other,)).fetchone())

    def read(filters: TicketListFilters, offset: int = 0):
        return views.list_ticket_summaries(
            tmp_db,
            page_request=ListPageRequest(limit=1, offset=offset),
            filters=filters,
            project_id=None,
            sprint_id=None,
            sprint_item_id=None,
        )

    chief = TicketListFilters(orchestrator=CHIEF_PRINCIPAL)
    pages = [read(chief, offset) for offset in (0, 1, 2)]
    assert all(page.match_count == 2 for page in pages)
    assert {row["id"] for page in pages for row in page.rows} == set(owned)
    assert pages[-1].rows == ()
    deferred = read(TicketListFilters(orchestrator=CHIEF_PRINCIPAL, is_backlogged=True))
    assert deferred.match_count == 1 and deferred.rows[0]["id"] == owned[1]
    for stage, expected in (("done", done), ("cancelled", retired)):
        terminal = read(
            TicketListFilters(orchestrator=CHIEF_PRINCIPAL, stages=(stage,), include_terminal=True)
        )
        assert terminal.match_count == 1 and terminal.rows[0]["id"] == expected
    scoped = read(TicketListFilters(orchestrator=Principal(PrincipalKind.sprint_item, "item_one")))
    assert scoped.match_count == 1 and scoped.rows[0]["id"] == item
    assert sprint_views.item_tickets(tmp_db, "item_one")[0]["is_backlogged"] is True
    assert dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (other,)).fetchone()) == before


@pytest.mark.parametrize(
    "query",
    [
        "orchestrator_kind=owner",
        "orchestrator_kind=ticket&orchestrator_id=t",
        "orchestrator_kind=chief&orchestrator_id=other",
        "orchestrator_kind=sprint_item",
        "orchestrator_id=chief",
    ],
)
def test_api_rejects_invalid_orchestrator_principals(tmp_path: Path, query: str) -> None:
    app, _ = _make_app(tmp_path)
    with TestClient(app) as client:
        response = client.get(f"/api/tickets?detail=summary&{query}")
    assert response.status_code == 400


def test_api_scopes_chief_and_item_before_paging_and_rejects_full_scope(tmp_path: Path) -> None:
    app, path = _make_app(tmp_path)
    conn = connect(str(path))
    seed_item(conn, "item_api")
    expected = seed(conn, title="Chief")
    item = seed(conn, title="Item", item="item_api")
    conn.close()
    with TestClient(app) as client:
        chief = client.get("/api/tickets?detail=summary&orchestrator_kind=chief&limit=1")
        scoped = client.get(
            "/api/tickets?detail=summary&orchestrator_kind=sprint_item&orchestrator_id=item_api"
        )
        empty = client.get("/api/tickets?detail=summary&orchestrator_kind=chief&offset=1&limit=1")
        full = client.get("/api/tickets?detail=full&orchestrator_kind=chief")
    assert chief.status_code == 200
    assert chief.json()["page"]["match_count"] == 1
    assert chief.json()["tickets"][0]["id"] == expected
    assert scoped.json()["tickets"][0]["id"] == item
    assert empty.json()["tickets"] == []
    assert empty.json()["page"]["match_count"] == 1
    assert full.status_code == 400
