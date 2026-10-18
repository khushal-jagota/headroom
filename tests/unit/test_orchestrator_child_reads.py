"""Scoped discovery retains quiet, deferred, completed, and retired children."""

from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL
from tests.unit.test_ticket_edit_api import _make_app

from planner.core.clock import TestClock as Clock
from planner.core.contracts import CHIEF_PRINCIPAL, JsonDict, Principal, PrincipalKind
from planner.core.db import connect
from planner.list_reads.contracts import ListPage, ListPageRequest
from planner.sprints import data as sprint_data
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


def seed_item(conn: Connection, title: str) -> str:
    return sprint_data.create_item(
        conn, title=title, project_id="project_vylo", clock=Clock(datetime(2026, 7, 1, tzinfo=UTC))
    ).id


def test_scope_precedes_counts_and_pagination_and_does_not_change_authority(
    tmp_db: Connection,
) -> None:
    item_one = seed_item(tmp_db, "item_one")
    item_two = seed_item(tmp_db, "item_two")
    other = seed(tmp_db, title="Other", item=item_two)
    owned = [seed(tmp_db, title="Quiet"), seed(tmp_db, title="Deferred", backlogged=True)]
    retired = seed(tmp_db, title="Retired", stage="cancelled")
    done = seed(tmp_db, title="Done", stage="done")
    item = seed(tmp_db, title="Item child", item=item_one, backlogged=True)
    before = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (other,)).fetchone())

    def read(filters: TicketListFilters, offset: int = 0) -> ListPage[JsonDict]:
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
    assert deferred.rows[0]["is_backlogged"] is True
    assert deferred.rows[0]["waiting_to_closeout"] is False
    for stage, expected in (("done", done), ("cancelled", retired)):
        terminal = read(
            TicketListFilters(orchestrator=CHIEF_PRINCIPAL, stages=(stage,), include_terminal=True)
        )
        assert terminal.match_count == 1 and terminal.rows[0]["id"] == expected
    scoped = read(TicketListFilters(orchestrator=Principal(PrincipalKind.sprint_item, item_one)))
    assert scoped.match_count == 1 and scoped.rows[0]["id"] == item
    assert sprint_views.item_tickets(tmp_db, item_one)[0]["is_backlogged"] is True
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
    item_api = seed_item(conn, "item_api")
    expected = seed(conn, title="Chief")
    item = seed(conn, title="Item", item=item_api)
    conn.close()
    with TestClient(app) as client:
        chief = client.get("/api/tickets?detail=summary&orchestrator_kind=chief&limit=1")
        scoped = client.get(
            f"/api/tickets?detail=summary&orchestrator_kind=sprint_item&orchestrator_id={item_api}"
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


def test_summary_preserves_canonical_consequences_wait(tmp_db: Connection) -> None:
    seed(tmp_db, title="Consequences", stage="needs_consequences")
    page = views.list_ticket_summaries(
        tmp_db,
        page_request=ListPageRequest(limit=30, offset=0),
        filters=TicketListFilters(orchestrator=CHIEF_PRINCIPAL),
        project_id=None,
        sprint_id=None,
        sprint_item_id=None,
    )
    assert page.rows[0]["waiting_to_closeout"] is True


def test_board_orchestrator_recap_uses_authoritative_body_without_changes(
    tmp_db: Connection,
) -> None:
    item = seed_item(tmp_db, "Recap owner")
    seed(tmp_db, title="Recap child", item=item)
    body = "  Ship the sidebar.\n\tKeep attention concise.  "
    tmp_db.execute("UPDATE sprint_items SET body=? WHERE id=?", (body, item))
    before = dict(tmp_db.execute("SELECT * FROM sprint_items WHERE id=?", (item,)).fetchone())

    board = views.board_view(tmp_db)

    assert board["sprint_items"][0]["recap_preview"] == "Ship the sidebar. Keep attention concise."
    after = dict(tmp_db.execute("SELECT * FROM sprint_items WHERE id=?", (item,)).fetchone())
    assert after == before


@pytest.mark.parametrize(
    ("recap", "expected"),
    [
        ("  First line.\n\tSecond   line.  ", "First line. Second line."),
        ("x" * 158 + "  " + "more text", "x" * 158 + "…"),
        (" \n\t", ""),
    ],
)
def test_board_and_child_recap_match_concise_ticket_summary(
    tmp_db: Connection, recap: str, expected: str
) -> None:
    item = seed_item(tmp_db, "Recap owner")
    ticket = seed(tmp_db, title="Recap child", item=item)
    tmp_db.execute("UPDATE tickets SET recap=? WHERE id=?", (recap, ticket))

    summary = views.list_ticket_summaries(
        tmp_db,
        page_request=ListPageRequest(limit=30, offset=0),
        filters=TicketListFilters(),
        project_id=None,
        sprint_id=None,
        sprint_item_id=None,
    )
    board = views.board_view(tmp_db)
    card = next(card for column in board["columns"] for card in column["cards"])
    child = board["sprint_items"][0]["tickets"][0]

    assert summary.rows[0]["recap_preview"] == expected
    assert card["recap_preview"] == expected
    assert child["recap_preview"] == expected
