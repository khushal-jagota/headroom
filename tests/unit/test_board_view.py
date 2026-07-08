"""Board view regressions."""

from __future__ import annotations

from sqlite3 import Connection

from planner.core.contracts import Priority
from planner.days.data import add_day_ticket
from planner.tickets.data import create_ticket
from planner.tickets.views import board_view


def _ticket(conn: Connection, title: str, now: int) -> str:
    ticket = create_ticket(
        conn,
        title=title,
        actor="human",
        now=now,
        title_max_chars=200,
        priority=Priority.P3,
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
