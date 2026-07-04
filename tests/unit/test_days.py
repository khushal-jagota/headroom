"""Days domain acceptance tests — items 1 (planning date §6.1), 12 (day-ticket
removal §3.4), 17 (plan tree §6.3), 18 (boundary job §6.2). The external boundary
is a local recording fake (no OS/network). Exactly one test per acceptance item
so the verify scorer's one-match rule stays satisfied."""

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
from planner.days.contracts import NodeStatus, PlanNode, PlanRoot, PlanTree
from planner.days.data import (
    add_day_ticket,
    apply_plan_effects,
    list_day_tickets,
    load_plan,
    materialize_day,
    remove_day_ticket,
    store_plan,
)
from planner.days.logic.dates import planning_date
from planner.days.logic.effects import AddTicketToDay, EmitEvent, ReplanChild, ReplanRoot
from planner.days.logic.tree import accept_all, invalidate_child, invalidate_root

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
    BoundaryInputs, and builds a proposed tree from inputs.carryover exactly like
    the shared FakeBoundaryAdapter."""

    calls: list[str] = field(default_factory=list)
    received: list[BoundaryInputs] = field(default_factory=list)

    def judgment(self, inputs: BoundaryInputs) -> BoundaryJudgment:
        self.calls.append("judgment")
        self.received.append(inputs)
        children = [
            PlanNode(
                ticket_id=entry.get("id"),
                note=str(entry.get("title", "")),
                status=NodeStatus.proposed,
                position=index,
            )
            for index, entry in enumerate(inputs.carryover)
        ]
        return BoundaryJudgment(
            brief_markdown=f"# Brief for {inputs.planning_date}",
            plan_tree=PlanTree(root=PlanRoot(focus="Recorded focus"), children=children),
        )

    def replan_root(self, day_id: str, inputs: BoundaryInputs) -> PlanTree:
        self.calls.append("replan_root")
        return PlanTree(root=PlanRoot(focus="Recorded replan"), children=[])

    def replan_child(self, day_id: str, child: PlanNode, inputs: BoundaryInputs) -> PlanNode:
        self.calls.append("replan_child")
        return child


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


def test_a17_plan_tree(tmp_db: Connection) -> None:
    conn = tmp_db
    tree = PlanTree(
        root=PlanRoot("focus", NodeStatus.proposed),
        children=[
            PlanNode("t_a", "", NodeStatus.proposed, 0),
            PlanNode("t_b", "", NodeStatus.proposed, 1),
            PlanNode(None, "note", NodeStatus.proposed, 2),
        ],
    )

    # Root invalidation → root + all children invalidated, exactly one replan (root).
    nt, eff = invalidate_root(tree)
    assert nt.root.status == NodeStatus.invalidated
    assert all(child.status == NodeStatus.invalidated for child in nt.children)
    assert EmitEvent(EventKind.plan_node_invalidated, {"node": "root"}) in eff
    replans = [e for e in eff if isinstance(e, (ReplanRoot, ReplanChild))]
    assert replans == [ReplanRoot()]

    # Child invalidation replaces only that child; other nodes keep status.
    nt2, eff2 = invalidate_child(tree, 1)
    assert nt2.children[1].status == NodeStatus.invalidated
    assert nt2.root.status == NodeStatus.proposed
    assert nt2.children[0].status == NodeStatus.proposed
    assert nt2.children[2].status == NodeStatus.proposed
    replans2 = [e for e in eff2 if isinstance(e, (ReplanRoot, ReplanChild))]
    assert replans2 == [ReplanChild(1)]
    assert EmitEvent(EventKind.plan_node_invalidated, {"node": 1}) in eff2

    # Accept-all + exactly-once day-list add (idempotent).
    _mk_ticket(conn, "t_a", state="needs_success", title="TA")
    _mk_ticket(conn, "t_b", state="needs_success", title="TB")
    now = 2000
    materialize_day(conn, "day_2026-07-05", now)
    add_day_ticket(conn, "day_2026-07-05", "t_b", now)  # pre-existing: [t_b@0]

    nt_all, eff_all = accept_all(tree)
    assert nt_all.root.status == NodeStatus.accepted
    assert all(child.status == NodeStatus.accepted for child in nt_all.children)
    assert EmitEvent(EventKind.plan_accepted_all, {}) in eff_all
    # Both ticket children add; the ticket_id=None child does NOT.
    adds = [e for e in eff_all if isinstance(e, AddTicketToDay)]
    assert adds == [AddTicketToDay("t_a"), AddTicketToDay("t_b")]

    apply_plan_effects(conn, "day_2026-07-05", nt_all, eff_all, now)

    # t_b appears exactly once (not re-added); t_a appended; positions contiguous.
    assert [(dt.ticket_id, dt.position) for dt in list_day_tickets(conn, "day_2026-07-05")] == [
        ("t_b", 0),
        ("t_a", 1),
    ]
    loaded = load_plan(conn, "day_2026-07-05")
    assert loaded is not None
    assert loaded.root.status == NodeStatus.accepted
    assert all(child.status == NodeStatus.accepted for child in loaded.children)


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
    run_boundary(conn, fake_clock, cfg, adapter1)

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
    # Proposed plan stored on the new day, built from carryover (one child, t_prog).
    proposed = [
        e
        for e in events
        if e.kind == EventKind.plan_proposed.value and e.entity_id == "day_2026-07-05"
    ]
    assert len(proposed) == 1
    plan = load_plan(conn, "day_2026-07-05")
    assert plan is not None
    assert len(plan.children) == 1
    assert plan.children[0].ticket_id == "t_prog"
    run_row = conn.execute(
        "SELECT judgment FROM boundary_runs WHERE planning_date = '2026-07-05'"
    ).fetchone()
    assert run_row is not None
    assert run_row["judgment"] == "ok"
    assert adapter1.calls == ["judgment"]

    # Part 2 — second tick same date does nothing (guarded).
    n_events = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
    n_runs = conn.execute("SELECT COUNT(*) AS n FROM boundary_runs").fetchone()["n"]
    run_boundary(conn, fake_clock, cfg, adapter1)
    assert conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"] == n_events
    assert conn.execute("SELECT COUNT(*) AS n FROM boundary_runs").fetchone()["n"] == n_runs
    assert adapter1.calls == ["judgment"]

    # Part 3 — explicit prior planning (a day-ticket) skips the judgment pass.
    fake_clock.set(datetime(2026, 7, 6, 5, 1).astimezone())
    add_day_ticket(conn, "day_2026-07-06", "t_prog", fake_clock.now_unix())
    adapter3 = RecordingBoundaryAdapter()
    run_boundary(conn, fake_clock, cfg, adapter3)
    assert adapter3.calls == []
    run_row3 = conn.execute(
        "SELECT judgment FROM boundary_runs WHERE planning_date = '2026-07-06'"
    ).fetchone()
    assert run_row3 is not None
    assert run_row3["judgment"] == "skipped"
    # The deterministic pass still ran: yesterday (day_2026-07-05) is closed.
    closed_05 = [
        e
        for e in read_events_since(conn, 0, 1000)
        if e.kind == EventKind.day_closed.value and e.entity_id == "day_2026-07-05"
    ]
    assert len(closed_05) == 1

    # Part 4 (A5) — an ACCEPTED plan node (no day-tickets) also skips judgment.
    fake_clock.set(datetime(2026, 7, 7, 5, 1).astimezone())
    accepted_tree = PlanTree(
        root=PlanRoot("focus", NodeStatus.proposed),
        children=[PlanNode("t_prog", "", NodeStatus.accepted, 0)],
    )
    store_plan(conn, "day_2026-07-07", accepted_tree, fake_clock.now_unix())
    adapter4 = RecordingBoundaryAdapter()
    run_boundary(conn, fake_clock, cfg, adapter4)
    assert adapter4.calls == []
    run_row4 = conn.execute(
        "SELECT judgment FROM boundary_runs WHERE planning_date = '2026-07-07'"
    ).fetchone()
    assert run_row4 is not None
    assert run_row4["judgment"] == "skipped"
