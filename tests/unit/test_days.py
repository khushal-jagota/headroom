"""Days domain acceptance tests: planning-date math and day-ticket behavior."""

from __future__ import annotations

from datetime import date, datetime
from sqlite3 import Connection

from planner.core.config import Config
from planner.core.contracts import EventKind
from planner.core.events import read_events_since
from planner.days.data import (
    add_day_ticket,
    list_day_tickets,
    remove_day_ticket,
)
from planner.days.logic.dates import planning_date


def _mk_ticket(
    conn: Connection,
    ticket_id: str,
    *,
    state: str,
    title: str,
    priority: str = "P3",
    deadline: str | None = None,
) -> None:
    """Insert the minimal NOT-NULL columns of a ticket; other columns use their
    schema defaults."""
    conn.execute(
        "INSERT INTO tickets (id, title, state, priority, deadline, project_id, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'project_vylo', 0, 0)",
        (ticket_id, title, state, priority, deadline),
    )


def test_a01_planning_date(cfg: Config) -> None:
    # 04:59 belongs to the previous planning date; 05:00 to the new one.
    assert planning_date(datetime(2026, 7, 5, 4, 59), cfg.boundary_hour) == date(2026, 7, 4)
    assert planning_date(datetime(2026, 7, 5, 5, 0), cfg.boundary_hour) == date(2026, 7, 5)
    # Boundary hour honored from config.
    assert cfg.boundary_hour == 5
    # Same instant, three hours → three results ⇒ the hour is load-bearing.
    assert planning_date(datetime(2026, 7, 5, 5, 0), 6) == date(2026, 7, 4)
    assert planning_date(datetime(2026, 7, 5, 5, 0), 0) == date(2026, 7, 5)


def test_a12_day_ticket_removal(tmp_db: Connection) -> None:
    conn = tmp_db
    _mk_ticket(conn, "t0", state="needs_success", title="T0")
    _mk_ticket(conn, "t1", state="in_progress", title="T1")
    _mk_ticket(conn, "t2", state="needs_success", title="T2")
    _mk_ticket(conn, "t3", state="needs_success", title="T3")
    now = 1000
    for tid in ("t0", "t1", "t2", "t3"):
        add_day_ticket(conn, "day_2026-07-04", tid, now)
    assert conn.execute("SELECT 1 FROM days WHERE id = 'day_2026-07-04'").fetchone() is not None
    assert [(dt.ticket_id, dt.position) for dt in list_day_tickets(conn, "day_2026-07-04")] == [
        ("t0", 0),
        ("t1", 1),
        ("t2", 2),
        ("t3", 3),
    ]

    remove_day_ticket(conn, "day_2026-07-04", "t1", now)  # a MIDDLE element

    # Association gone; positions re-packed contiguously in original relative order.
    assert [(dt.ticket_id, dt.position) for dt in list_day_tickets(conn, "day_2026-07-04")] == [
        ("t0", 0),
        ("t2", 1),
        ("t3", 2),
    ]
    # Ticket state is untouched (this is "deferring").
    state = conn.execute("SELECT state FROM tickets WHERE id = 't1'").fetchone()["state"]
    assert state == "in_progress"
    # Exactly one day_ticket_removed event, payload {ticket_id: t1}.
    removed = [
        e
        for e in read_events_since(conn, 0, 1000)
        if e.kind == EventKind.day_ticket_removed.value and e.entity_id == "day_2026-07-04"
    ]
    assert len(removed) == 1
    assert removed[0].payload == {"ticket_id": "t1"}
