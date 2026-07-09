"""Board view regressions."""

from __future__ import annotations

from sqlite3 import Connection

from planner.core.contracts import Priority
from planner.days.data import add_day_ticket
from planner.sprints.data import create_item
from planner.tickets.data import create_ticket
from planner.tickets.views import board_view


def _ticket(
    conn: Connection,
    title: str,
    now: int,
    *,
    project_id: str | None = None,
    sprint_item_id: str | None = None,
) -> str:
    ticket = create_ticket(
        conn,
        title=title,
        actor="human",
        now=now,
        title_max_chars=200,
        project_id=project_id,
        priority=Priority.P3,
        sprint_item_id=sprint_item_id,
    )
    return ticket.id


def test_board_view_only_returns_tickets_on_requested_day(tmp_db: Connection) -> None:
    today = _ticket(tmp_db, "Today board ticket", 1)
    other_day = _ticket(tmp_db, "Other day ticket", 2)
    _ticket(tmp_db, "Backlog ticket", 3)
    add_day_ticket(tmp_db, "day_2026-07-04", today, 10)
    add_day_ticket(tmp_db, "day_2026-07-03", other_day, 10)

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    titles = [
        card["title"]
        for column in board["columns"]
        for card in column["cards"]
    ]

    assert titles == ["Today board ticket"]


def test_board_view_groups_parented_ticket_by_parent_item_project(
    tmp_db: Connection, fake_clock
) -> None:
    item = create_item(
        tmp_db,
        title="Parent item",
        project_id="project_vylo",
        clock=fake_clock,
    )
    parented = _ticket(tmp_db, "Parented ticket", 1, sprint_item_id=item.id)
    standalone = _ticket(tmp_db, "Standalone ticket", 2, project_id="project_learning")
    unprojected = _ticket(tmp_db, "Unprojected ticket", 3)
    for ticket_id in (parented, standalone, unprojected):
        add_day_ticket(tmp_db, "day_2026-07-04", ticket_id, 10)

    board = board_view(tmp_db, 20, day_id="day_2026-07-04")
    cards = {
        card["title"]: card
        for column in board["columns"]
        for card in column["cards"]
    }

    assert cards["Parented ticket"]["project_id"] is None
    assert cards["Parented ticket"]["project"] is None
    assert cards["Parented ticket"]["group_project_id"] == "project_vylo"
    assert cards["Parented ticket"]["group_project"] == "Vylo"
    assert cards["Standalone ticket"]["group_project_id"] == "project_learning"
    assert cards["Standalone ticket"]["group_project"] == "Learning"
    assert cards["Unprojected ticket"]["group_project_id"] is None
    assert cards["Unprojected ticket"]["group_project"] is None
