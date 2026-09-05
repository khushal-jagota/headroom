"""Sprint date integrity and Outcome blocker facts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from sqlite3 import Connection
from threading import Barrier, Event

import pytest

from planner.core import links as core_links
from planner.core.authctx import _classify, require_planning_write
from planner.core.clock import TestClock
from planner.core.contracts import LinkKind
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.sprints.data import (
    create_idea,
    create_item,
    create_sprint,
    read_item,
    read_sprint,
    set_sprint_dates,
    update_sprint,
)
from planner.sprints.logic import DateRange, current_sprint_id

_EMPTY_CODING_FIELDS = "{}"


def _insert_ticket(
    conn: Connection,
    ticket_id: str,
    stage: str,
    *,
    sprint_item_id: str | None = None,
    ticket_status: str = "empty",
) -> None:
    # T04 owns ticket writers; a direct INSERT is the sanctioned test-fixture
    # shortcut for setting child/blocker ticket-states (only NOT-NULL non-defaulted
    # columns are supplied; project may stay NULL under its CHECK).
    conn.execute(
        "INSERT INTO tickets (id, title, worker_type, employee_backend, stage, "
        "sprint_item_id, ceiling, "
        "ticket_status, field_values, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'hermes', ?, ?, 'needs_success', ?, ?, 0, 0)",
        (ticket_id, "child", stage, sprint_item_id, ticket_status, _EMPTY_CODING_FIELDS),
    )


def _set_ticket_state(conn: Connection, ticket_id: str, stage: str) -> None:
    # Same sanction as _insert_ticket: blocker ticket-states are test fixtures here,
    # not exercises of T04's writers.
    conn.execute("UPDATE tickets SET stage = ? WHERE id = ?", (stage, ticket_id))


# --- item 10: sprint-item permissions (single anchored test) ----------------------


# --- item 20: sprint overlap (single anchored test) -------------------------------


def test_a20_sprint_overlap(tmp_db: Connection, fake_clock: TestClock) -> None:
    # Freeze + weekly_addenda are retired (rev6): every sprint text field is
    # always-editable and nothing latches, so the only rule left to assert is sprint
    # overlap rejection — interior overlap, inclusive boundary (candidate start ==
    # existing end), and the adjacent day succeeds.
    sp = create_sprint(
        tmp_db, name="S", date_start="2026-07-01", date_end="2026-07-14", clock=fake_clock
    )
    with pytest.raises(PlannerError) as ei3:
        create_sprint(
            tmp_db, name="B", date_start="2026-07-10", date_end="2026-07-20", clock=fake_clock
        )
    assert ei3.value.code == ErrorCode.sprint_overlap
    assert ei3.value.detail["conflict_id"] == sp.id
    with pytest.raises(PlannerError) as ei4:
        create_sprint(
            tmp_db, name="C", date_start="2026-07-14", date_end="2026-07-21", clock=fake_clock
        )
    assert ei4.value.code == ErrorCode.sprint_overlap
    assert ei4.value.detail["conflict_id"] == sp.id
    d = create_sprint(
        tmp_db, name="D", date_start="2026-07-15", date_end="2026-07-21", clock=fake_clock
    )
    assert read_sprint(tmp_db, d.id).id == d.id


# --- supplementary sprints-domain tests (no fence anchor) -------------------------


def test_x06_current_sprint_selection() -> None:
    a = DateRange(id="sp_a", date_start="2026-07-01", date_end="2026-07-14")
    d = DateRange(id="sp_d", date_start="2026-07-15", date_end="2026-07-21")

    assert current_sprint_id("2026-07-10", [a, d]) == "sp_a"
    assert current_sprint_id("2026-07-14", [a, d]) == "sp_a"  # inclusive end
    assert current_sprint_id("2026-07-15", [a, d]) == "sp_d"
    assert current_sprint_id("2026-06-30", [a, d]) is None


def test_outcome_retains_direct_blockers_and_cleared_facts(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    directly_blocked = create_item(
        tmp_db, title="directly blocked", project_id="project_vylo", clock=fake_clock
    )
    _insert_ticket(tmp_db, "t_done_direct", "done")
    _insert_ticket(tmp_db, "t_dropped_direct", "dropped")
    core_links.add_link(tmp_db, "t_done_direct", directly_blocked.id, LinkKind.blocks, 1)
    core_links.add_link(tmp_db, "t_dropped_direct", directly_blocked.id, LinkKind.blocks, 1)

    direct_read = read_item(tmp_db, directly_blocked.id)

    assert direct_read.blocking_ticket_ids == ["t_done_direct", "t_dropped_direct"]
    assert direct_read.blockers_cleared is True

    child_blocked = create_item(
        tmp_db, title="child cleared", project_id="project_vylo", clock=fake_clock
    )
    _insert_ticket(tmp_db, "t_child_cleared", "needs_success", sprint_item_id=child_blocked.id)
    _insert_ticket(tmp_db, "t_done_child_blocker", "done")
    core_links.add_link(tmp_db, "t_done_child_blocker", "t_child_cleared", LinkKind.blocks, 1)


def test_x06_create_idea_writer_logs_event(tmp_db: Connection, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()
    idea = create_idea(
        tmp_db,
        title="Maybe later",
        body="Worth exploring.",
        project_id="project_vylo",
        now=now,
    )

    assert idea["title"] == "Maybe later"
    assert idea["body"] == "Worth exploring."
    assert idea["project_id"] == "project_vylo"
    assert idea["project_name"] == "Vylo"


def test_x06_set_sprint_dates_writer_updates_and_rejects_overlap(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    sprint = create_sprint(
        tmp_db, name="A", date_start="2026-07-01", date_end="2026-07-14", clock=fake_clock
    )
    other = create_sprint(
        tmp_db, name="B", date_start="2026-07-20", date_end="2026-07-22", clock=fake_clock
    )

    updated = set_sprint_dates(
        tmp_db,
        sprint.id,
        date_start="2026-07-02",
        date_end="2026-07-15",
        clock=fake_clock,
    )

    assert updated.date_start == "2026-07-02"
    assert updated.date_end == "2026-07-15"
    assert read_sprint(tmp_db, sprint.id).date_start == "2026-07-02"
    assert read_sprint(tmp_db, sprint.id).date_end == "2026-07-15"

    with pytest.raises(PlannerError) as exc:
        set_sprint_dates(
            tmp_db,
            sprint.id,
            date_start=None,
            date_end="2026-07-20",
            clock=fake_clock,
        )
    assert exc.value.code is ErrorCode.sprint_overlap
    assert exc.value.detail["conflict_id"] == other.id


def test_compound_sprint_updates_serialize_overlap_validation_with_the_write(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    first = create_sprint(
        tmp_db, name="A", date_start="2026-07-01", date_end="2026-07-05", clock=fake_clock
    )
    second = create_sprint(
        tmp_db, name="B", date_start="2026-07-16", date_end="2026-07-20", clock=fake_clock
    )
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    start_together = Barrier(2)

    def attempt(sprint_id: str, date_start: str, date_end: str) -> str:
        conn = connect(db_path)
        try:
            start_together.wait()
            try:
                update_sprint(
                    conn,
                    sprint_id,
                    text_edits={},
                    set_dates=True,
                    date_start=date_start,
                    date_end=date_end,
                    clock=fake_clock,
                )
            except PlannerError as exc:
                assert exc.code is ErrorCode.sprint_overlap
                return "overlap"
            return "updated"
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(attempt, first.id, "2026-07-01", "2026-07-12"),
            executor.submit(attempt, second.id, "2026-07-10", "2026-07-20"),
        )
        results = sorted(future.result() for future in futures)

    assert results == ["overlap", "updated"]
    ranges = {
        first.id: (
            read_sprint(tmp_db, first.id).date_start,
            read_sprint(tmp_db, first.id).date_end,
        ),
        second.id: (
            read_sprint(tmp_db, second.id).date_start,
            read_sprint(tmp_db, second.id).date_end,
        ),
    }
    assert ranges in (
        {
            first.id: ("2026-07-01", "2026-07-12"),
            second.id: ("2026-07-16", "2026-07-20"),
        },
        {
            first.id: ("2026-07-01", "2026-07-05"),
            second.id: ("2026-07-10", "2026-07-20"),
        },
    )


def test_planning_claim_and_sprint_write_share_one_write_lock(
    tmp_db: Connection, fake_clock: TestClock
) -> None:
    sprint = create_sprint(
        tmp_db, name="A", date_start="2026-07-01", date_end="2026-07-14", clock=fake_clock
    )
    _insert_ticket(tmp_db, "t_planning_sprint", "needs_success")
    tmp_db.execute(
        "UPDATE tickets SET worker_type = 'planning-sprint' WHERE id = 't_planning_sprint'"
    )
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    admitted = Event()
    deletion_attempted = Event()
    deletion_done = Event()
    ctx = _classify("worker", "t_planning_sprint")

    def write_as_planning_worker() -> None:
        conn = connect(db_path)
        try:

            def admit() -> None:
                require_planning_write(conn, ctx, "planning-sprint")
                admitted.set()
                assert deletion_attempted.wait(timeout=5)
                assert not deletion_done.is_set()

            update_sprint(
                conn,
                sprint.id,
                text_edits={"primary_bet": "Ship the generalized surface."},
                set_dates=False,
                date_start=None,
                date_end=None,
                clock=fake_clock,
                admit=admit,
            )
        finally:
            conn.close()

    def revoke_claim() -> None:
        assert admitted.wait(timeout=5)
        conn = connect(db_path)
        try:
            deletion_attempted.set()
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM tickets WHERE id = 't_planning_sprint'")
            conn.execute("COMMIT")
            deletion_done.set()
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        writer = executor.submit(write_as_planning_worker)
        revoker = executor.submit(revoke_claim)
        writer.result()
        revoker.result()

    assert read_sprint(tmp_db, sprint.id).primary_bet == "Ship the generalized surface."
    assert tmp_db.execute("SELECT 1 FROM tickets WHERE id = 't_planning_sprint'").fetchone() is None
