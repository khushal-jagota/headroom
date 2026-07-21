"""Acceptance items 10 and 20 for the sprints domain.

The §18.3 fence is 1:1 — one named test per item — so test_a10_* and test_a20_*
each exist exactly once and assert their item's full statement. Supplementary
coverage runs under the unanchored test_x06_* names.

Item 10 — sprint-item status is derived from child tickets and blocking links:
done has precedence when all non-dropped children are done; in_progress follows
running/control states or active ticket lifecycle states; blocked follows open
blocking links, blocked children, or errored child runtime; dropped children are
ignored and all-dropped/no-children items are todo.

Item 20 — sprint overlap: freeze and weekly_addenda are retired (rev6), so every
sprint text field is always-editable and nothing latches; the remaining rule is
sprint overlap rejection (inclusive ranges).
"""

from __future__ import annotations

import pytest

from planner.core import links as core_links
from planner.core.contracts import LinkKind
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import read_events_since
from planner.sprints.contracts import ItemStatus
from planner.sprints.data import (
    create_idea,
    create_item,
    create_sprint,
    read_item,
    read_sprint,
    set_sprint_dates,
)
from planner.sprints.logic import DateRange, current_sprint_id
from planner.tickets.contracts import TicketFields
from planner.tickets.logic.fields_codec import fields_to_json
from planner.worker_types.coding import CODING_WORKER_TYPE_DEFINITION

_EMPTY_CODING_FIELDS = fields_to_json(TicketFields.empty(CODING_WORKER_TYPE_DEFINITION.field_ids()))


def _insert_ticket(
    conn,
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
        "ticket_status, fields, created_at, updated_at) "
        "VALUES (?, ?, 'coding', 'hermes', ?, ?, 'needs_success', ?, ?, 0, 0)",
        (ticket_id, "child", stage, sprint_item_id, ticket_status, _EMPTY_CODING_FIELDS),
    )


def _set_ticket_state(conn, ticket_id: str, stage: str) -> None:
    # Same sanction as _insert_ticket: blocker ticket-states are test fixtures here,
    # not exercises of T04's writers.
    conn.execute("UPDATE tickets SET stage = ? WHERE id = ?", (stage, ticket_id))


def _events(conn, entity_id: str, kind: str) -> list[dict]:
    return [
        e.payload
        for e in read_events_since(conn, 0, 100_000)
        if e.entity_id == entity_id and e.kind == kind
    ]


# --- item 10: sprint-item permissions (single anchored test) ----------------------


def test_a10_sprint_item_permissions(tmp_db, fake_clock) -> None:
    empty = create_item(tmp_db, title="empty", project_id="project_vylo", clock=fake_clock)
    assert read_item(tmp_db, empty.id).status is ItemStatus.todo

    done = create_item(tmp_db, title="done", project_id="project_vylo", clock=fake_clock)
    _insert_ticket(tmp_db, "t_done_a", "done", sprint_item_id=done.id)
    _insert_ticket(tmp_db, "t_done_b", "dropped", sprint_item_id=done.id)
    assert read_item(tmp_db, done.id).status is ItemStatus.done

    running = create_item(tmp_db, title="running", project_id="project_vylo", clock=fake_clock)
    _insert_ticket(
        tmp_db,
        "t_running",
        "needs_success",
        sprint_item_id=running.id,
        ticket_status="agent_running_step",
    )
    assert read_item(tmp_db, running.id).status is ItemStatus.in_progress

    shaped = create_item(tmp_db, title="shaped", project_id="project_vylo", clock=fake_clock)
    _insert_ticket(tmp_db, "t_shaped", "needs_plan", sprint_item_id=shaped.id)
    assert read_item(tmp_db, shaped.id).status is ItemStatus.in_progress

    blocked = create_item(tmp_db, title="blocked", project_id="project_vylo", clock=fake_clock)
    _insert_ticket(tmp_db, "t_a", "needs_implementation")
    _insert_ticket(tmp_db, "t_b", "done")
    core_links.add_link(tmp_db, "t_a", blocked.id, LinkKind.blocks, fake_clock.now_unix())
    core_links.add_link(tmp_db, "t_b", blocked.id, LinkKind.blocks, fake_clock.now_unix())
    blocked_read = read_item(tmp_db, blocked.id)
    assert blocked_read.status is ItemStatus.blocked
    assert blocked_read.blocking_ticket_ids == ["t_a", "t_b"]
    assert blocked_read.blockers_cleared is False
    _set_ticket_state(tmp_db, "t_a", "done")
    cleared_read = read_item(tmp_db, blocked.id)
    assert cleared_read.blockers_cleared is True
    assert cleared_read.status is ItemStatus.todo

    child_blocked = create_item(
        tmp_db, title="child blocked", project_id="project_vylo", clock=fake_clock
    )
    _insert_ticket(tmp_db, "t_child", "needs_success", sprint_item_id=child_blocked.id)
    _insert_ticket(tmp_db, "t_child_blocker", "needs_success")
    core_links.add_link(
        tmp_db, "t_child_blocker", "t_child", LinkKind.blocks, fake_clock.now_unix()
    )
    assert read_item(tmp_db, child_blocked.id).status is ItemStatus.blocked

    errored = create_item(tmp_db, title="errored", project_id="project_vylo", clock=fake_clock)
    _insert_ticket(
        tmp_db,
        "t_errored_child",
        "needs_success",
        sprint_item_id=errored.id,
        ticket_status="errored",
    )
    assert read_item(tmp_db, errored.id).status is ItemStatus.blocked

    all_dropped = create_item(tmp_db, title="dropped", project_id="project_vylo", clock=fake_clock)
    _insert_ticket(tmp_db, "t_dropped", "dropped", sprint_item_id=all_dropped.id)
    assert read_item(tmp_db, all_dropped.id).status is ItemStatus.todo


# --- item 20: sprint overlap (single anchored test) -------------------------------


def test_a20_sprint_overlap(tmp_db, fake_clock) -> None:
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


def test_x06_sprint_item_blocked_status_uses_active_blocker_summary(tmp_db, fake_clock) -> None:
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
    assert direct_read.status is ItemStatus.todo

    child_blocked = create_item(
        tmp_db, title="child cleared", project_id="project_vylo", clock=fake_clock
    )
    _insert_ticket(tmp_db, "t_child_cleared", "needs_success", sprint_item_id=child_blocked.id)
    _insert_ticket(tmp_db, "t_done_child_blocker", "done")
    core_links.add_link(tmp_db, "t_done_child_blocker", "t_child_cleared", LinkKind.blocks, 1)

    assert read_item(tmp_db, child_blocked.id).status is ItemStatus.todo


def test_x06_child_ticket_blocked_status_uses_canonical_blocker_summary(
    tmp_db, fake_clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = create_item(
        tmp_db, title="child summary item", project_id="project_vylo", clock=fake_clock
    )
    _insert_ticket(tmp_db, "t_child", "needs_success", sprint_item_id=item.id)
    _insert_ticket(tmp_db, "t_child_blocker", "needs_success")
    core_links.add_link(tmp_db, "t_child_blocker", "t_child", LinkKind.blocks, 1)
    original_blocker_summary = core_links.blocker_summary
    summary_calls: list[str] = []

    def tracked_blocker_summary(conn, entity_id: str):
        summary_calls.append(entity_id)
        return original_blocker_summary(conn, entity_id)

    monkeypatch.setattr(core_links, "blocker_summary", tracked_blocker_summary)

    assert read_item(tmp_db, item.id).status is ItemStatus.blocked
    assert item.id in summary_calls
    assert "t_child" in summary_calls


def test_x06_create_idea_writer_logs_event(tmp_db, fake_clock) -> None:
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
    assert _events(tmp_db, idea["id"], "idea_created") == [
        {"title": "Maybe later", "source": "api"}
    ]


def test_x06_set_sprint_dates_writer_updates_and_rejects_overlap(tmp_db, fake_clock) -> None:
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
    assert _events(tmp_db, sprint.id, "sprint_updated") == [
        {"field": "date_start", "from": "2026-07-01", "to": "2026-07-02"},
        {"field": "date_end", "from": "2026-07-14", "to": "2026-07-15"},
    ]

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
