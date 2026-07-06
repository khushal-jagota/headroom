"""Days domain acceptance tests — items 1 (planning date §6.1), 12 (day-ticket
removal §3.4), 18 (boundary job §6.2). The external boundary is a local recording
fake (no OS/network). Exactly one test per acceptance item so the verify scorer's
one-match rule stays satisfied."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from sqlite3 import Connection
from typing import TYPE_CHECKING

from planner.core.adapters.base import BoundaryInputs, BoundaryJudgment
from planner.core.config import Config
from planner.core.contracts import EventKind
from planner.core.events import read_events_since
from planner.days.boundary import run_boundary
from planner.days.data import (
    add_day_ticket,
    list_day_tickets,
    read_day,
    remove_day_ticket,
)
from planner.days.logic.dates import planning_date

if TYPE_CHECKING:
    from planner.core.clock import TestClock


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
        "INSERT INTO tickets (id, title, state, priority, deadline, project, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'Vylo', 0, 0)",
        (ticket_id, title, state, priority, deadline),
    )


@dataclass
class RecordingBoundaryAdapter:
    """A recording fake (no OS/network): records method names AND the received
    BoundaryInputs, and fills the four overview fields keyed off the planning date
    exactly like the shared FakeBoundaryAdapter."""

    calls: list[str] = field(default_factory=list)
    received: list[BoundaryInputs] = field(default_factory=list)

    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
        self.calls.append("judgment")
        self.received.append(inputs)
        return BoundaryJudgment(
            focus=f"Focus for {inputs.planning_date}",
            brief_take=f"Take for {inputs.planning_date}",
            watchout=f"Watch for {inputs.planning_date}",
            if_today_lands=f"Lands for {inputs.planning_date}",
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


def test_a18_boundary_job(tmp_db: Connection, fake_clock: TestClock, cfg: Config) -> None:
    conn = tmp_db

    # Part 1 — first tick ≥ 05:00 creates the day + carryover + overdue + close.
    _mk_ticket(conn, "t_done", state="done", title="Done one")
    _mk_ticket(
        conn, "t_prog", state="in_progress", title="Prog one", priority="P1",
        deadline="2026-07-01",
    )
    add_day_ticket(conn, "day_2026-07-04", "t_done", 100)
    add_day_ticket(conn, "day_2026-07-04", "t_prog", 100)
    fake_clock.set(datetime(2026, 7, 5, 5, 1).astimezone())
    adapter1 = RecordingBoundaryAdapter()
    assert run_boundary(conn, fake_clock, cfg, adapter1) == "ok"

    assert conn.execute("SELECT 1 FROM days WHERE id = 'day_2026-07-05'").fetchone() is not None
    events = read_events_since(conn, 0, 1000)
    created = [
        e
        for e in events
        if e.kind == EventKind.day_created.value and e.entity_id == "day_2026-07-05"
    ]
    assert len(created) == 1
    # Carryover excludes t_done (done); keeps t_prog.
    assert adapter1.received[0].carryover == [
        {"id": "t_prog", "title": "Prog one", "state": "in_progress", "priority": "P1"}
    ]
    # Overdue: t_prog (deadline 2026-07-01 < 2026-07-05, not terminal); t_done excluded.
    assert adapter1.received[0].overdue == [
        {"id": "t_prog", "title": "Prog one", "state": "in_progress", "priority": "P1"}
    ]
    # day_closed on YESTERDAY with the done/not-done split.
    closed = [
        e
        for e in events
        if e.kind == EventKind.day_closed.value and e.entity_id == "day_2026-07-04"
    ]
    assert len(closed) == 1
    assert closed[0].payload == {"done_count": 1, "not_done_count": 1}
    # The four overview fields the boundary filled are stored on the new day.
    day05 = read_day(conn, "day_2026-07-05", 1)
    assert day05.focus == "Focus for 2026-07-05"
    assert day05.brief_take == "Take for 2026-07-05"
    assert day05.watchout == "Watch for 2026-07-05"
    assert day05.if_today_lands == "Lands for 2026-07-05"
    # The overview write emits exactly one day_updated {field: "overview"} as the WS
    # refetch signal.
    overview = [
        e
        for e in events
        if e.kind == EventKind.day_updated.value and e.entity_id == "day_2026-07-05"
    ]
    assert len(overview) == 1
    assert overview[0].payload == {"field": "overview", "cause": "boundary"}
    assert adapter1.calls == ["judgment"]

    # Part 2 — second tick same date is a guarded no-op: the next day is already
    # materialized, so run_boundary returns None and appends no events.
    n_events = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    assert run_boundary(conn, fake_clock, cfg, adapter1) is None
    assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == n_events
    assert adapter1.calls == ["judgment"]

    # Part 3 — a human-placed day-ticket materializes the new day, so the next tick
    # hits the materialization guard first (removal-plan §14.4): run_boundary returns
    # None and never calls judgment. day_2026-07-05 is not re-closed.
    fake_clock.set(datetime(2026, 7, 6, 5, 1).astimezone())
    add_day_ticket(conn, "day_2026-07-06", "t_prog", fake_clock.now_unix())
    adapter3 = RecordingBoundaryAdapter()
    assert run_boundary(conn, fake_clock, cfg, adapter3) is None
    assert adapter3.calls == []
    closed_05 = [
        e
        for e in read_events_since(conn, 0, 1000)
        if e.kind == EventKind.day_closed.value and e.entity_id == "day_2026-07-05"
    ]
    assert closed_05 == []
