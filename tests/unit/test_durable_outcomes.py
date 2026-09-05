"""Outcomes retain context while exact Sprint commitments and work move independently."""

from __future__ import annotations

from sqlite3 import Connection

import pytest

from planner.core.authctx import _classify, require_planning_write, require_ticket_worker_write
from planner.core.clock import TestClock as Clock
from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.sprints import commitments, data, views
from planner.sprints.supervisor_service import require_current_child
from planner.tickets import data as tickets
from planner.tickets.contracts import Ticket


def _ticket(conn: Connection, outcome_id: str, sprint_id: str, title: str) -> Ticket:
    return tickets.create_ticket(
        conn,
        title=title,
        actor="human",
        now=1,
        title_max_chars=200,
        worker_type="coding",
        sprint_item_id=outcome_id,
        sprint_id=sprint_id,
    )


def test_two_sprint_outcome_journey_preserves_history_context_and_exact_partition(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    first = data.create_sprint(
        tmp_db, name="First", date_start="2026-07-01", date_end="2026-07-07", clock=fake_clock
    )
    second = data.create_sprint(
        tmp_db, name="Second", date_start="2026-07-08", date_end="2026-07-14", clock=fake_clock
    )
    assert tmp_db.execute("SELECT count(*) FROM sprint_items").fetchone()[0] == 0
    outcome = data.create_item(
        tmp_db,
        title="Durable intent",
        body="Keep this context",
        project_id="project_vylo",
        clock=fake_clock,
    )

    def admit() -> None:
        pass

    commitments.set_commitment(tmp_db, first.id, outcome.id, committed=True, admit=admit)
    empty = views.sprint_tracking_view(tmp_db, first.id, "2026-07-04")
    assert empty["outcome_groups"][0]["tickets"] == []
    done = _ticket(tmp_db, outcome.id, first.id, "Completed history")
    chosen = _ticket(tmp_db, outcome.id, first.id, "Selected work")
    left = _ticket(tmp_db, outcome.id, first.id, "Not selected")
    tmp_db.execute("UPDATE tickets SET stage='done' WHERE id=?", (done.id,))
    before_done = dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (done.id,)).fetchone())
    result = commitments.carry_outcome(
        tmp_db, first.id, second.id, outcome.id, [chosen.id], actor="human", now=2, admit=admit
    )
    assert result["ticket_ids"] == [chosen.id]
    assert (
        commitments.carry_outcome(
            tmp_db, first.id, second.id, outcome.id, [chosen.id], actor="human", now=3, admit=admit
        )
        == result
    )
    assert (
        dict(tmp_db.execute("SELECT * FROM tickets WHERE id=?", (done.id,)).fetchone())
        == before_done
    )
    assert tickets.read_ticket(tmp_db, left.id).sprint_id == first.id
    assert tickets.read_ticket(tmp_db, chosen.id).sprint_item_id == outcome.id
    assert data.read_item(tmp_db, outcome.id).item == outcome
    assert [
        t["id"]
        for t in views.sprint_tracking_view(tmp_db, second.id, "2026-07-09")["outcome_groups"][0][
            "tickets"
        ]
    ] == [chosen.id]
    commitments.set_commitment(tmp_db, second.id, outcome.id, committed=False, admit=admit)
    other = views.sprint_tracking_view(tmp_db, second.id, "2026-07-09")
    assert other["outcome_groups"][0]["committed"] is False
    workspace = views.item_workspace(tmp_db, outcome.id, "day_2026-07-04")
    assert "rollup" not in workspace and "status" not in workspace and "sprint_id" not in workspace
    assert {t["id"]: (t["sprint_id"], t["sprint_name"]) for t in workspace["tickets"]} == {
        done.id: (first.id, "First"),
        chosen.id: (second.id, "Second"),
        left.id: (first.id, "First"),
    }
    tickets.unclassify_ticket(tmp_db, chosen.id, sprint_item_id=outcome.id, actor="human", now=4)
    assert tickets.read_ticket(tmp_db, chosen.id).sprint_id == second.id
    assert [
        t["id"]
        for t in views.sprint_tracking_view(tmp_db, second.id, "2026-07-09")["unclassified_tickets"]
    ] == [chosen.id]
    tickets.classify_ticket(tmp_db, chosen.id, sprint_item_id=outcome.id, actor="human", now=5)
    tickets.edit_ticket(
        tmp_db, chosen.id, edit={"sprint_id": None}, title_max_chars=200, actor="human", now=6
    )
    assert tickets.read_ticket(tmp_db, chosen.id).sprint_item_id == outcome.id
    assert (
        next(
            t
            for t in views.item_workspace(tmp_db, outcome.id, "day_2026-07-04")["tickets"]
            if t["id"] == chosen.id
        )["sprint_name"]
        is None
    )


def test_carry_rejects_entire_stale_selection_and_preserves_existing_authority(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    first = data.create_sprint(
        tmp_db, name="First", date_start="2026-07-01", date_end="2026-07-07", clock=fake_clock
    )
    second = data.create_sprint(
        tmp_db, name="Second", date_start="2026-07-08", date_end="2026-07-14", clock=fake_clock
    )
    outcome = data.create_item(tmp_db, title="O", project_id="project_vylo", clock=fake_clock)
    other = data.create_item(tmp_db, title="Other", project_id="project_vylo", clock=fake_clock)
    valid = _ticket(tmp_db, outcome.id, first.id, "Valid")
    stale = _ticket(tmp_db, outcome.id, first.id, "Completed since selection")
    commitments.set_commitment(tmp_db, first.id, outcome.id, committed=True, admit=lambda: None)
    tmp_db.execute("UPDATE tickets SET stage='done' WHERE id=?", (stale.id,))
    with pytest.raises(PlannerError, match="no longer eligible"):
        commitments.carry_outcome(
            tmp_db,
            first.id,
            second.id,
            outcome.id,
            [valid.id, stale.id],
            actor="human",
            now=2,
            admit=lambda: None,
        )
    assert tickets.read_ticket(tmp_db, valid.id).sprint_id == first.id
    assert (
        tmp_db.execute("SELECT 1 FROM sprint_outcomes WHERE sprint_id=?", (second.id,)).fetchone()
        is None
    )
    worker = _classify("worker", valid.id)
    require_ticket_worker_write(tmp_db, worker)
    with pytest.raises(PlannerError):
        commitments.set_commitment(
            tmp_db,
            second.id,
            outcome.id,
            committed=True,
            admit=lambda: require_planning_write(tmp_db, worker, "planning-sprint"),
        )
    # Classification retains the existing broad Ticket-backed Worker authority.
    tickets.classify_ticket(
        tmp_db,
        stale.id,
        sprint_item_id=other.id,
        actor="worker",
        now=3,
        admit=lambda: require_ticket_worker_write(tmp_db, worker),
    )
    assert tickets.read_ticket(tmp_db, stale.id).sprint_item_id == other.id
    supervisor = _classify("sprint_item_supervisor", None, outcome.id)
    tickets.edit_ticket(
        tmp_db, valid.id, edit={"sprint_id": second.id}, title_max_chars=200, actor="human", now=3
    )
    assert require_current_child(tmp_db, supervisor, outcome.id, valid.id).id == valid.id
    tickets.classify_ticket(tmp_db, valid.id, sprint_item_id=other.id, actor="human", now=4)
    with pytest.raises(PlannerError):
        require_current_child(tmp_db, supervisor, outcome.id, valid.id)


def test_creation_validates_explicit_placement_and_blockers_without_an_outcome(
    tmp_db: Connection,
) -> None:
    with pytest.raises(PlannerError, match="invalid sprint_id"):
        tickets.create_ticket(
            tmp_db,
            title="Invalid Sprint",
            actor="human",
            now=1,
            title_max_chars=200,
            worker_type="coding",
            project_id="project_vylo",
            sprint_id="missing",
        )
    with pytest.raises(PlannerError):
        tickets.create_ticket(
            tmp_db,
            title="Invalid blocker",
            actor="human",
            now=1,
            title_max_chars=200,
            worker_type="coding",
            blocked_by_ticket_ids=["missing"],
        )
    with pytest.raises(PlannerError, match="invalid sprint_id"):
        tickets.create_ticket_from_external_work(
            tmp_db,
            title="Invalid external Sprint",
            kickoff_note="External context",
            target_stage="needs_success",
            provided_values={},
            actor="chief",
            now=1,
            title_max_chars=200,
            worker_type="coding",
            project_id="project_vylo",
            sprint_id="missing",
        )
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0


def test_tracking_reads_without_a_writer_lock_and_preserves_the_callers_snapshot(
    tmp_db: Connection,
    fake_clock: Clock,
) -> None:
    sprint = data.create_sprint(
        tmp_db,
        name="Committed name",
        date_start="2026-07-01",
        date_end="2026-07-07",
        clock=fake_clock,
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    writer = connect(path, busy_timeout_ms=20)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE sprints SET name='Uncommitted name' WHERE id=?", (sprint.id,))
        current = views.sprint_current_view(tmp_db, "2026-07-04")
        assert current["sprint"] is not None
        assert current["sprint"]["name"] == "Committed name"
        assert not tmp_db.in_transaction
        tmp_db.execute("BEGIN")
        explicit = views.sprint_tracking_view(tmp_db, sprint.id, "2026-07-04")
        assert explicit == current
        assert tmp_db.in_transaction
        tmp_db.execute("ROLLBACK")
    finally:
        writer.execute("ROLLBACK")
        writer.close()
