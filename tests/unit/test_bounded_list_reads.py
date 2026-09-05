"""Bounded summary reads for agent-facing list commands."""

from __future__ import annotations

from sqlite3 import Connection


from planner.core.clock import TestClock as ClockForTest
from planner.list_reads.contracts import ListPageRequest
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
