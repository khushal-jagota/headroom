"""Acceptance items 10 and 20 for the sprints domain.

The §18.3 fence is 1:1 — one named test per item — so test_a10_* and test_a20_*
each exist exactly once and assert their item's full statement. Supplementary
coverage (supersede mechanics, empty-blockers rejection, current-sprint
selection) runs under the unanchored test_x06_* names.

Item 10 — sprint-item permissions: agent todo->active succeeds; agent direct
active->done write rejected; done via proposal + accept succeeds; blocked_by
stored and blockers_cleared computed when all blockers done.

Item 20 — sprint overlap: freeze and weekly_addenda are retired (rev6), so every
sprint text field is always-editable and nothing latches; the remaining rule is
sprint overlap rejection (inclusive ranges).
"""

from __future__ import annotations

import inspect

import pytest

from planner.core.contracts import Project
from planner.core.errors import ErrorCode, PlannerError
from planner.core.events import read_events_since
from planner.sprints.contracts import ItemStatus
from planner.sprints.data import (
    accept_item_status,
    create_item,
    create_sprint,
    propose_item_status,
    read_item,
    read_sprint,
    transition_item_status,
)
from planner.sprints.logic import DateRange, current_sprint_id


def _insert_ticket(conn, ticket_id: str, state: str) -> None:
    # T04 owns ticket writers; a direct INSERT is the sanctioned test-fixture
    # shortcut for setting blocker ticket-states (only NOT-NULL non-defaulted
    # columns are supplied; project may stay NULL under its CHECK).
    conn.execute(
        "INSERT INTO tickets (id, title, state, created_at, updated_at) VALUES (?, ?, ?, 0, 0)",
        (ticket_id, "blk", state),
    )


def _set_ticket_state(conn, ticket_id: str, state: str) -> None:
    # Same sanction as _insert_ticket: blocker ticket-states are test fixtures here,
    # not exercises of T04's writers.
    conn.execute("UPDATE tickets SET state = ? WHERE id = ?", (state, ticket_id))


def _events(conn, entity_id: str, kind: str) -> list[dict]:
    return [
        e.payload
        for e in read_events_since(conn, 0, 100_000)
        if e.entity_id == entity_id and e.kind == kind
    ]


# --- item 10: sprint-item permissions (single anchored test) ----------------------


def test_a10_sprint_item_permissions(tmp_db, fake_clock) -> None:
    # Leg 1 — agent todo -> active succeeds, with one exact {from, to, cause} event.
    item1 = create_item(tmp_db, title="ship it", project=Project.Vylo, clock=fake_clock)
    assert item1.status is ItemStatus.todo
    moved = transition_item_status(tmp_db, item1.id, ItemStatus.active, clock=fake_clock)
    assert moved.status is ItemStatus.active
    assert _events(tmp_db, item1.id, "item_status_changed") == [
        {"from": "todo", "to": "active", "cause": "agent"},
    ]

    # Leg 2 — agent direct active -> done write rejected (and deferred_next_sprint
    # equally, per PROPOSAL_ONLY_STATUSES); status and event log unchanged.
    item2 = create_item(tmp_db, title="x", project=Project.Vylo, clock=fake_clock)
    transition_item_status(tmp_db, item2.id, ItemStatus.active, clock=fake_clock)
    with pytest.raises(PlannerError) as ei:
        transition_item_status(tmp_db, item2.id, ItemStatus.done, clock=fake_clock)
    assert ei.value.code == ErrorCode.item_transition_forbidden
    with pytest.raises(PlannerError) as ei2:
        transition_item_status(
            tmp_db, item2.id, ItemStatus.deferred_next_sprint, clock=fake_clock
        )
    assert ei2.value.code == ErrorCode.item_transition_forbidden
    assert read_item(tmp_db, item2.id).item.status is ItemStatus.active
    changes = _events(tmp_db, item2.id, "item_status_changed")
    assert all(c["to"] != "done" for c in changes)
    assert all(c["to"] != "deferred_next_sprint" for c in changes)

    # Leg 3 — done via proposal + human accept succeeds; proposal cleared; exact
    # proposal_filed / proposal_accepted / item_status_changed payloads.
    item3 = create_item(tmp_db, title="y", project=Project.Vylo, clock=fake_clock)
    transition_item_status(tmp_db, item3.id, ItemStatus.active, clock=fake_clock)
    proposed = propose_item_status(
        tmp_db, item3.id, ItemStatus.done, note="ready", proposed_by="agent-x", clock=fake_clock
    )
    assert proposed.status is ItemStatus.active
    assert proposed.status_proposal is not None
    assert proposed.status_proposal.to_status is ItemStatus.done
    assert _events(tmp_db, item3.id, "proposal_filed") == [
        {
            "field": "status",
            "body": {"to_status": "done", "note": "ready"},
            "proposed_by": "agent-x",
        },
    ]
    accepted = accept_item_status(tmp_db, item3.id, clock=fake_clock)
    assert accepted.status is ItemStatus.done
    assert accepted.status_proposal is None
    assert _events(tmp_db, item3.id, "proposal_accepted") == [
        {
            "field": "status",
            "body": {"to_status": "done"},
            "resolved_by": "human",
            "edited": False,
        },
    ]
    assert {"from": "active", "to": "done", "cause": "accept"} in _events(
        tmp_db, item3.id, "item_status_changed"
    )
    # §4.4.7 guard: item accepts carry no onward-grant pair.
    params = inspect.signature(accept_item_status).parameters
    assert "next_ceiling" not in params
    assert "at_cap" not in params

    # Leg 4 — blocked_by stored; blockers_cleared computed on read, flipping True
    # only when every blocker ticket reaches state done.
    _insert_ticket(tmp_db, "t_a", "in_progress")
    _insert_ticket(tmp_db, "t_b", "done")
    item4 = create_item(tmp_db, title="z", project=Project.Vylo, clock=fake_clock)
    blocked = transition_item_status(
        tmp_db, item4.id, ItemStatus.blocked, clock=fake_clock, blocked_by=["t_a", "t_b"]
    )
    assert blocked.status is ItemStatus.blocked
    assert blocked.blocked_by == ["t_a", "t_b"]
    assert _events(tmp_db, item4.id, "item_status_changed") == [
        {"from": "todo", "to": "blocked", "cause": "agent"},
    ]
    assert read_item(tmp_db, item4.id).blockers_cleared is False
    _set_ticket_state(tmp_db, "t_a", "done")
    assert read_item(tmp_db, item4.id).blockers_cleared is True


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


def test_x06_item_proposal_supersedes_prior(tmp_db, fake_clock) -> None:
    item = create_item(tmp_db, title="x", project=Project.Vylo, clock=fake_clock)
    transition_item_status(tmp_db, item.id, ItemStatus.active, clock=fake_clock)

    propose_item_status(
        tmp_db, item.id, ItemStatus.done, note="d1", proposed_by="agent-x", clock=fake_clock
    )
    superseded = propose_item_status(
        tmp_db,
        item.id,
        ItemStatus.deferred_next_sprint,
        note="d2",
        proposed_by="agent-x",
        clock=fake_clock,
    )

    supersede_events = _events(tmp_db, item.id, "proposal_superseded")
    assert supersede_events == [
        {
            "field": "status",
            "replaced_body": {
                "to_status": "done",
                "note": "d1",
                "proposed_by": "agent-x",
                "created_at": fake_clock.now_unix(),
            },
        },
    ]

    assert superseded.status is ItemStatus.active
    assert superseded.status_proposal is not None
    assert superseded.status_proposal.to_status is ItemStatus.deferred_next_sprint

    # Accept the surviving proposal -> deferred_next_sprint, proposal cleared.
    accepted = accept_item_status(tmp_db, item.id, clock=fake_clock)
    assert accepted.status is ItemStatus.deferred_next_sprint
    assert accepted.status_proposal is None


def test_x06_item_blocked_requires_blockers(tmp_db, fake_clock) -> None:
    item = create_item(tmp_db, title="x", project=Project.Vylo, clock=fake_clock)

    with pytest.raises(PlannerError) as ei:
        transition_item_status(
            tmp_db, item.id, ItemStatus.blocked, clock=fake_clock, blocked_by=[]
        )
    assert ei.value.code == ErrorCode.validation
    assert read_item(tmp_db, item.id).item.status is ItemStatus.todo


def test_x06_current_sprint_selection() -> None:
    a = DateRange(id="sp_a", date_start="2026-07-01", date_end="2026-07-14")
    d = DateRange(id="sp_d", date_start="2026-07-15", date_end="2026-07-21")

    assert current_sprint_id("2026-07-10", [a, d]) == "sp_a"
    assert current_sprint_id("2026-07-14", [a, d]) == "sp_a"  # inclusive end
    assert current_sprint_id("2026-07-15", [a, d]) == "sp_d"
    assert current_sprint_id("2026-06-30", [a, d]) is None
