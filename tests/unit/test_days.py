"""Days domain acceptance tests: planning-date math and day-ticket behavior."""

from __future__ import annotations

from datetime import date, datetime
from sqlite3 import Connection

from planner.core.config import Config
from planner.days.data import (
    add_day_ticket,
    list_day_tickets,
    remove_day_ticket,
)
from planner.days.logic.dates import planning_date
from planner.tickets.contracts import TicketFields
from planner.tickets.logic.fields_codec import fields_to_json
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

_EMPTY_CODING_FIELDS = fields_to_json(TicketFields.empty(CODING_WORKER_TYPE_DEFINITION.field_ids()))


def _mk_ticket(
    conn: Connection,
    ticket_id: str,
    *,
    stage: str,
    title: str,
    priority: str = "P3",
    deadline: str | None = None,
) -> None:
    """Insert the minimal NOT-NULL columns of a ticket; other columns use their
    schema defaults."""
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, "
        "priority, deadline, project_id, "
        "ceiling, fields, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'hermes', ?, ?, ?, 'project_vylo', 'needs_success', ?, 0, 0)",
        (ticket_id, title, stage, priority, deadline, _EMPTY_CODING_FIELDS),
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
    _mk_ticket(conn, "t0", stage="needs_success", title="T0")
    _mk_ticket(conn, "t1", stage="needs_implementation", title="T1")
    _mk_ticket(conn, "t2", stage="needs_success", title="T2")
    _mk_ticket(conn, "t3", stage="needs_success", title="T3")
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
    # Ticket Stage is untouched (this is "deferring").
    stage = conn.execute("SELECT stage FROM tickets WHERE id = 't1'").fetchone()["stage"]
    assert stage == "needs_implementation"
