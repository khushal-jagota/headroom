"""Scoped discovery retains quiet, deferred, completed, children."""

from datetime import UTC, datetime
from sqlite3 import Connection

from tests.support.principals import OWNER_PRINCIPAL

from planner.core.clock import TestClock as Clock
from planner.sprints import data as sprint_data
from planner.tickets import data


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
