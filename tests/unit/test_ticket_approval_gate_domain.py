"""Focused domain proofs for the one approval gate.

A parked proposal is awaiting approval, and that is the only park there is. These prove
what scope does — set a ceiling and say what happens at it — and, just as importantly,
what it no longer does: it does not move ownership, it does not move a parked proposal,
and it does not constrain where a Ticket may be placed.
"""

from __future__ import annotations

from sqlite3 import Connection

import pytest

from planner.core.clock import TestClock
from planner.core.contracts import Priority
from planner.core.errors import PlannerError
from planner.sprints import data as sprints_data
from planner.tickets import data
from planner.tickets.contracts import (
    TITLE_MAX_CHARS,
    AtCap,
    StageOwnershipMode,
    TicketStatus,
)


def _ticket_under_a_sprint_item(conn: Connection, clock: TestClock) -> str:
    item = sprints_data.create_item(
        conn,
        title="Review owner",
        project_id="project_vylo",
        priority=Priority.P2,
        clock=clock,
    )
    ticket = data.create_ticket(
        conn,
        title="A supervised Ticket",
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="Start here.",
        worker_type="coding",
        sprint_item_id=item.id,
    )
    ticket = data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=clock.now_unix(),
        next_ceiling="needs_success",
        at_cap=AtCap.propose,
    )
    assert ticket.stage == "needs_success"
    return ticket.id


# --- the single gate -----------------------------------------------------------


def test_a_parked_proposal_awaits_approval(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    ticket_id = _ticket_under_a_sprint_item(tmp_db, fake_clock)

    parked = data.file_proposal(
        tmp_db,
        ticket_id,
        field="success",
        body="Verified result.",
        actor="worker-run",
        now=fake_clock.now_unix(),
    )

    assert parked.ticket_status is TicketStatus.awaiting_approval


def test_a_scope_change_leaves_a_parked_proposal_where_it_is(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    """There is nowhere else for a parked proposal to go.

    Scope used to decide who reviewed a proposal, so changing it moved one already
    parked. With one gate there is no second reviewer to hand it to, and the proposal
    keeps standing exactly where it stood.
    """
    ticket_id = _ticket_under_a_sprint_item(tmp_db, fake_clock)
    data.file_proposal(
        tmp_db,
        ticket_id,
        field="success",
        body="Verified result.",
        actor="worker-run",
        now=fake_clock.now_unix(),
    )

    rescoped = data.change_scope(
        tmp_db,
        ticket_id,
        ceiling="needs_plan",
        at_cap=AtCap.stop,
        actor="human",
        now=fake_clock.now_unix(),
    )

    assert rescoped.ticket_status is TicketStatus.awaiting_approval
    assert rescoped.fields.slots["success"].proposal is not None


# --- what scope no longer reaches ----------------------------------------------


def test_a_paired_stage_stays_paired_whatever_the_scope(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    """Scope is the leash, not the ownership of a Stage.

    A paired Stage used to collapse to worker-owned under agent-review scope, because
    the reviewer was an agent and no user was in the loop. There is no such route now,
    so a paired Stage is paired under every scope a Ticket can hold.
    """
    ticket_id = _ticket_under_a_sprint_item(tmp_db, fake_clock)
    tmp_db.execute(
        "UPDATE tickets SET default_stage_ownership_mode='paired', "
        "ticket_status='paired' WHERE id=?",
        (ticket_id,),
    )
    tmp_db.commit()

    for at_cap in (AtCap.stop, AtCap.propose):
        rescoped = data.change_scope(
            tmp_db,
            ticket_id,
            ceiling="needs_success",
            at_cap=at_cap,
            actor="human",
            now=fake_clock.now_unix(),
        )
        assert rescoped.effective_stage_ownership_mode is StageOwnershipMode.paired
        assert rescoped.ticket_status is TicketStatus.paired


def test_scope_does_not_constrain_where_a_ticket_is_placed(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    """Agent review had to sit under the normal Sprint Item that owned its reviewer.

    Nothing owns a reviewer now, so a Ticket goes wherever its owner puts it.
    """
    ticket_id = _ticket_under_a_sprint_item(tmp_db, fake_clock)
    ticket = data.read_ticket(tmp_db, ticket_id)
    assert ticket.sprint_item_id is not None

    data.move_ticket_to_backlog(
        tmp_db,
        ticket_id,
        sprint_item_id=ticket.sprint_item_id,
        actor="human",
        now=fake_clock.now_unix(),
    )

    assert data.read_ticket(tmp_db, ticket_id).sprint_item_id is None


# --- who may resolve, and against which proposal -------------------------------


def test_revision_write_does_not_clear_a_proposal_that_changed_after_delivery_check(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    ticket_id = _ticket_under_a_sprint_item(tmp_db, fake_clock)
    original = (
        data.file_proposal(
            tmp_db,
            ticket_id,
            field="success",
            body="Original result.",
            actor="worker-run",
            now=fake_clock.now_unix(),
        )
        .fields.slots["success"]
        .proposal
    )
    assert original is not None
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='conv-review-race' WHERE id=?",
        (ticket_id,),
    )
    tmp_db.commit()
    data.file_proposal(
        tmp_db,
        ticket_id,
        field="success",
        body="Newer result.",
        actor="worker-run",
        now=fake_clock.now_unix() + 1,
    )

    with pytest.raises(PlannerError, match="proposal changed"):
        data.return_for_revision(
            tmp_db,
            ticket_id,
            message="Please revise.",
            actor="human",
            now=fake_clock.now_unix() + 2,
            expected_proposal=original,
        )

    current = data.read_ticket(tmp_db, ticket_id).fields.slots["success"].proposal
    assert current is not None
    assert current.body == "Newer result."


def test_supervisor_resolution_rechecks_the_current_parent_in_the_writer(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    """Which Tickets a supervisor may write to at all is unchanged by the one gate."""
    ticket_id = _ticket_under_a_sprint_item(tmp_db, fake_clock)
    parked = data.file_proposal(
        tmp_db,
        ticket_id,
        field="success",
        body="Verified result.",
        actor="worker-run",
        now=fake_clock.now_unix(),
    )
    old_item_id = parked.sprint_item_id
    assert old_item_id is not None
    new_item = sprints_data.create_item(
        tmp_db,
        title="New review owner",
        project_id="project_vylo",
        priority=Priority.P2,
        clock=fake_clock,
    )
    tmp_db.execute(
        "UPDATE tickets SET sprint_item_id=? WHERE id=?",
        (new_item.id, ticket_id),
    )
    tmp_db.commit()

    with pytest.raises(PlannerError, match="not available"):
        data.accept_proposal(
            tmp_db,
            ticket_id,
            field="success",
            actor="sprint_item_supervisor",
            now=fake_clock.now_unix(),
            next_ceiling="needs_approach",
            at_cap=AtCap.propose,
            supervisor_sprint_item_id=old_item_id,
        )

    assert data.read_ticket(tmp_db, ticket_id).fields.slots["success"].proposal is not None
