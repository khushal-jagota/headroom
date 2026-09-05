"""Bounded summary reads for agent-facing list commands."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from sqlite3 import Connection

from fastapi.testclient import TestClient

from planner.core.clock import TestClock as ClockForTest
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.list_reads.contracts import ListPageRequest
from planner.list_reads.logic import select_page
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views
from planner.tickets.contracts import (
    FieldSlot,
    Proposal,
    TicketFields,
    TicketListFilters,
    TicketStatus,
)
from planner.tickets.logic.fields_codec import fields_to_json
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION


def _ticket(conn: Connection, clock: ClockForTest, title: str) -> str:
    return tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title=title,
        actor="human",
        now=clock.now_unix(),
        title_max_chars=200,
    ).id


def _search_fields(*, value: str = "", proposal: str = "") -> str:
    slots = {
        field_id: FieldSlot() for field_id in CODING_WORKER_TYPE_DEFINITION.field_ids()
    }
    slots["success"] = FieldSlot(
        value=value or None,
        proposal=(Proposal(proposal, "agent", 1) if proposal else None),
    )
    return fields_to_json(TicketFields(slots))


def test_shared_page_facts_are_exact_for_middle_and_empty_pages() -> None:
    middle = select_page(["a", "b", "c", "d"], ListPageRequest(limit=2, offset=1))
    assert middle.rows == ("b", "c")
    assert middle.facts_json() == {
        "match_count": 4,
        "return_count": 2,
        "limit": 2,
        "offset": 1,
        "omitted_before": 1,
        "omitted_after": 1,
        "complete": False,
        "next_offset": 3,
    }

    empty = select_page(["a"], ListPageRequest(limit=2, offset=4))
    assert empty.rows == ()
    assert empty.omitted_before == 1
    assert empty.omitted_after == 0
    assert empty.complete is False
    assert empty.next_offset is None


def test_ticket_summary_filters_search_and_bounds_before_selection(
    tmp_db: Connection, fake_clock: ClockForTest
) -> None:
    title_id = _ticket(tmp_db, fake_clock, "Title Needle")
    recap_id = _ticket(tmp_db, fake_clock, "Recap source")
    value_id = _ticket(tmp_db, fake_clock, "Value source")
    proposal_id = _ticket(tmp_db, fake_clock, "Proposal source")
    note_id = _ticket(tmp_db, fake_clock, "Note source")
    terminal_id = _ticket(tmp_db, fake_clock, "Terminal Needle")
    tmp_db.execute(
        "UPDATE tickets SET recap = ? WHERE id = ?", ("Recap Needle", recap_id)
    )
    for ticket_id, fields in (
        (value_id, _search_fields(value="Value Needle")),
        (proposal_id, _search_fields(proposal="Proposal Needle")),
    ):
        tmp_db.execute(
            "UPDATE tickets SET fields = ? WHERE id = ?", (fields, ticket_id)
        )
    tmp_db.execute("UPDATE tickets SET guidance = ? WHERE id = ?", ("Note Needle", note_id))
    tmp_db.execute(
        "UPDATE tickets SET stage = 'done', ticket_status = 'empty' WHERE id = ?",
        (terminal_id,),
    )
    tmp_db.execute(
        "UPDATE tickets SET stage = 'needs_success' WHERE id = ?", (proposal_id,)
    )
    tmp_db.execute(
        "UPDATE tickets SET ticket_status = 'errored' WHERE id = ?", (note_id,)
    )

    for needle, expected in (
        ("title needle", title_id),
        ("RECAP NEEDLE", recap_id),
        ("value needle", value_id),
        ("proposal needle", proposal_id),
        ("note needle", note_id),
    ):
        page = tickets_views.list_ticket_summaries(
            tmp_db,
            page_request=ListPageRequest(),
            filters=TicketListFilters(search=needle),
            project_id=None,
            sprint_id=None,
            sprint_item_id=None,
        )
        assert [row["id"] for row in page.rows] == [expected]

    default_page = tickets_views.list_ticket_summaries(
        tmp_db,
        page_request=ListPageRequest(limit=1),
        filters=TicketListFilters(
            stages=("needs_kickoff", "needs_success"),
            excluded_stages=("needs_success",),
            ticket_statuses=(TicketStatus.awaiting_approval, TicketStatus.errored),
            excluded_ticket_statuses=(TicketStatus.errored,),
            search="source",
        ),
        project_id=None,
        sprint_id=None,
        sprint_item_id=None,
    )
    assert default_page.match_count == 2
    assert default_page.return_count == 1
    assert default_page.omitted_after == 1
    source_ids = sorted((recap_id, value_id))
    assert [row["id"] for row in default_page.rows] == source_ids[:1]
    assert terminal_id not in {row["id"] for row in default_page.rows}

    tmp_db.execute(
        "INSERT INTO sprint_items "
        "(id, title, project_id, priority, created_at, updated_at) "
        "VALUES ('si_search', 'Search item', 'project_vylo', 'P1', 1, 1)"
    )
    tmp_db.execute(
        "UPDATE tickets SET project_id = 'project_vylo', sprint_id = NULL, "
        "sprint_item_id = 'si_search' WHERE id = ?",
        (recap_id,),
    )
    placed = tickets_views.list_ticket_summaries(
        tmp_db,
        page_request=ListPageRequest(),
        filters=TicketListFilters(
            stages=("needs_kickoff", "needs_success"),
            ticket_statuses=(TicketStatus.awaiting_approval, TicketStatus.errored),
            excluded_ticket_statuses=(TicketStatus.errored,),
            search="source",
        ),
        project_id="project_vylo",
        sprint_id="null",
        sprint_item_id="si_search",
    )
    assert [row["id"] for row in placed.rows] == [recap_id]

    terminal_page = tickets_views.list_ticket_summaries(
        tmp_db,
        page_request=ListPageRequest(),
        filters=TicketListFilters(
            stages=("done",), include_terminal=True, search="needle"
        ),
        project_id=None,
        sprint_id=None,
        sprint_item_id=None,
    )
    assert [row["id"] for row in terminal_page.rows] == [terminal_id]


def test_summary_endpoints_are_bounded_and_rich_browser_reads_stay_rich(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "bounded-api.db"
    with connect(str(db_path)) as conn:
        create_schema(conn)
        conn.execute(
            "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
            "VALUES ('sp_one', 'One', '2026-07-01', '2026-07-14', 1, 1)"
        )
        conn.execute(
            "INSERT INTO sprints (id, name, date_start, date_end, created_at, updated_at) "
            "VALUES ('sp_two', 'Two', '2026-07-15', '2026-07-28', 2, 2)"
        )
        conn.execute(
            "INSERT INTO sprint_items "
            "(id, title, project_id, priority, created_at, updated_at) "
            "VALUES ('si_one', 'One item', 'project_vylo', 'P1', 1, 1)"
        )
        conn.execute(
            "INSERT INTO sprint_items "
            "(id, title, project_id, priority, created_at, updated_at) "
            "VALUES ('si_two', 'Two item', 'project_vylo', 'P0', 2, 2)"
        )
        ticket_id = _ticket(
            conn,
            ClockForTest(datetime(2026, 7, 4, 12, 0, 0).astimezone()),
            "One ticket",
        )
        second_ticket_id = _ticket(
            conn,
            ClockForTest(datetime(2026, 7, 4, 12, 0, 1).astimezone()),
            "Two ticket",
        )
        conn.execute(
            "INSERT INTO days (id, created_at, updated_at) VALUES ('day_2026-07-04', 1, 1)"
        )
        conn.execute(
            "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, 0)",
            ("day_2026-07-04", ticket_id),
        )
        conn.execute(
            "INSERT INTO day_tickets (day_id, ticket_id, position) VALUES (?, ?, 1)",
            ("day_2026-07-04", second_ticket_id),
        )
        for index in range(32):
            conn.execute(
                "INSERT INTO projects "
                "(id, name, summary, priority, created_at, updated_at) "
                "VALUES (?, ?, '', NULL, 1, 1)",
                (f"project_extra_{index:02}", f"Extra {index:02}"),
            )
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
        },
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))

    with TestClient(app) as client:
        default_projects = client.get("/api/project-summaries").json()
        assert default_projects["page"] == {
            "match_count": 36,
            "return_count": 30,
            "limit": 30,
            "offset": 0,
            "omitted_before": 0,
            "omitted_after": 6,
            "complete": False,
            "next_offset": 30,
        }
        for path, key in (
            ("/api/ticket-summaries?limit=1", "tickets"),
            ("/api/sprint-summaries?limit=1", "sprints"),
            ("/api/sprint-item-summaries?limit=1", "items"),
            ("/api/project-summaries?limit=1", "projects"),
            ("/api/day/2026-07-04/tickets?limit=1", "tickets"),
        ):
            response = client.get(path)
            assert response.status_code == 200, response.text
            body = response.json()
            assert len(body[key]) <= 1
            assert body["page"]["limit"] == 1
            assert body["page"]["return_count"] == len(body[key])

        terminal_without_flag = client.get(
            "/api/ticket-summaries", params={"stage": "done"}
        )
        unknown_stage = client.get(
            "/api/ticket-summaries", params={"stage": "not_a_stage"}
        )
        assert terminal_without_flag.status_code == 400
        assert terminal_without_flag.json()["error"]["code"] == "validation"
        assert unknown_stage.status_code == 200
        assert unknown_stage.json()["tickets"] == []
        assert unknown_stage.json()["page"]["complete"] is True

        assert [
            row["id"]
            for row in client.get("/api/sprint-summaries").json()["sprints"]
        ] == ["sp_two", "sp_one"]
        assert [
            row["id"]
            for row in client.get("/api/sprint-item-summaries").json()["items"]
        ] == ["si_two", "si_one"]
        assert [
            row["id"]
            for row in client.get("/api/day/2026-07-04/tickets").json()["tickets"]
        ] == [ticket_id, second_ticket_id]
        project_rows = client.get("/api/project-summaries").json()["projects"]
        assert [row["id"] for row in project_rows[:3]] == [
            "project_extra_00",
            "project_extra_01",
            "project_extra_02",
        ]

        rich_ticket = client.get("/api/tickets").json()["tickets"][0]
        rich_sprint = client.get("/api/sprints").json()["sprints"][0]
        rich_item = client.get("/api/items").json()["items"][0]
        rich_project = client.get("/api/projects").json()["projects"][0]
        rich_day = client.get("/api/day/2026-07-04").json()["tickets"][0]

    assert "fields" in rich_ticket
    assert "kickoff" in rich_sprint
    assert "body" in rich_item
    assert "summary" in rich_project
    assert "agent_working" in rich_day
