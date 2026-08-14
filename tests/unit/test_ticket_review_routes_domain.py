"""Focused domain proofs for Ticket review routes and proposal snapshots."""

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
    ProposalReviewRoute,
    StageOwnershipMode,
    TicketStatus,
)
from planner.tickets.logic import machine
from planner.worker_types.new_worker import NEW_WORKER_TYPE_DEFINITION


def _agent_review_ticket(conn: Connection, clock: TestClock) -> str:
    item = sprints_data.create_item(
        conn,
        title="Review owner",
        project_id="project_vylo",
        priority=Priority.P2,
        clock=clock,
    )
    ticket = data.create_ticket(
        conn,
        title="Agent review",
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
        at_cap=AtCap.agent_review,
    )
    assert ticket.stage == "needs_success"
    return ticket.id


def test_agent_review_requires_a_normal_sprint_item(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Unsupervised",
        actor="human",
        now=fake_clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="Start here.",
        worker_type="coding",
    )
    with pytest.raises(PlannerError, match="normal Sprint Item"):
        data.accept_proposal(
            tmp_db,
            ticket.id,
            field="kickoff",
            actor="human",
            now=fake_clock.now_unix(),
            next_ceiling="needs_success",
            at_cap=AtCap.agent_review,
        )


def test_scope_route_change_reconciles_the_resting_status(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    ticket_id = _agent_review_ticket(tmp_db, fake_clock)
    tmp_db.execute(
        "UPDATE tickets SET default_stage_ownership_mode='paired', "
        "at_cap='user_review', ticket_status='paired' WHERE id=?",
        (ticket_id,),
    )
    tmp_db.commit()

    delegated = data.change_scope(
        tmp_db,
        ticket_id,
        ceiling="needs_success",
        at_cap=AtCap.agent_review,
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert delegated.ticket_status is TicketStatus.empty

    restored = data.change_scope(
        tmp_db,
        ticket_id,
        ceiling="needs_success",
        at_cap=AtCap.user_review,
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert restored.ticket_status is TicketStatus.paired


def test_scope_change_moves_the_proposal_already_parked(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    ticket_id = _agent_review_ticket(tmp_db, fake_clock)
    parked = data.file_proposal(
        tmp_db,
        ticket_id,
        field="success",
        body="Verified result.",
        actor="worker-run",
        now=fake_clock.now_unix(),
    )
    assert parked.ticket_status is TicketStatus.awaiting_agent_review

    handed_over = data.change_scope(
        tmp_db,
        ticket_id,
        ceiling="needs_success",
        at_cap=AtCap.user_review,
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert handed_over.ticket_status is TicketStatus.awaiting_user_review
    assert handed_over.fields.slots["success"].proposal is not None

    taken_back = data.change_scope(
        tmp_db,
        ticket_id,
        ceiling="needs_success",
        at_cap=AtCap.agent_review,
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert taken_back.ticket_status is TicketStatus.awaiting_agent_review


def test_handing_review_to_the_user_makes_a_paired_stage_paired_again(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    """The user who reviews the Stage is the user who works in it.

    Under agent review a paired Stage collapses to worker-owned, because no user is in
    the loop. Handing review back restores both the reviewer and the pairing.
    """
    ticket_id = _agent_review_ticket(tmp_db, fake_clock)
    tmp_db.execute(
        "UPDATE tickets SET default_stage_ownership_mode='paired' WHERE id=?",
        (ticket_id,),
    )
    tmp_db.commit()
    parked = data.file_proposal(
        tmp_db,
        ticket_id,
        field="success",
        body="Verified result.",
        actor="worker-run",
        now=fake_clock.now_unix(),
    )
    assert parked.effective_stage_ownership_mode is StageOwnershipMode.worker
    assert parked.ticket_status is TicketStatus.awaiting_agent_review

    handed_over = data.change_scope(
        tmp_db,
        ticket_id,
        ceiling="needs_success",
        at_cap=AtCap.user_review,
        actor="human",
        now=fake_clock.now_unix(),
    )
    assert handed_over.effective_stage_ownership_mode is StageOwnershipMode.paired
    assert handed_over.ticket_status is TicketStatus.awaiting_user_review


def test_revision_write_does_not_clear_a_proposal_that_changed_after_delivery_check(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    ticket_id = _agent_review_ticket(tmp_db, fake_clock)
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
    ticket_id = _agent_review_ticket(tmp_db, fake_clock)
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
            at_cap=AtCap.user_review,
            supervisor_sprint_item_id=old_item_id,
        )

    assert data.read_ticket(tmp_db, ticket_id).fields.slots["success"].proposal is not None


def test_agent_review_scope_cannot_be_orphaned_by_placement_writes(
    tmp_db: Connection,
    fake_clock: TestClock,
) -> None:
    ticket_id = _agent_review_ticket(tmp_db, fake_clock)
    ticket = data.read_ticket(tmp_db, ticket_id)
    assert ticket.sprint_item_id is not None

    with pytest.raises(PlannerError, match="normal Sprint Item"):
        data.edit_ticket(
            tmp_db,
            ticket_id,
            edit={"sprint_item_id": None},
            title_max_chars=TITLE_MAX_CHARS,
            actor="human",
            now=fake_clock.now_unix(),
        )
    with pytest.raises(PlannerError, match="normal Sprint Item"):
        data.move_ticket_to_backlog(
            tmp_db,
            ticket_id,
            sprint_item_id=ticket.sprint_item_id,
            actor="human",
            now=fake_clock.now_unix(),
        )

    assert data.read_ticket(tmp_db, ticket_id).sprint_item_id == ticket.sprint_item_id


def test_agent_review_converts_only_paired_defaults_through_ceiling() -> None:
    definition = NEW_WORKER_TYPE_DEFINITION
    converted = machine.effective_stage_ownership_mode(
        "needs_understanding",
        {},
        worker_type_definition=definition,
        default_stage_ownership_mode=StageOwnershipMode.paired,
        ceiling="needs_stages",
        at_cap=AtCap.agent_review,
    )
    beyond_ceiling = machine.effective_stage_ownership_mode(
        "needs_runtime_defaults",
        {},
        worker_type_definition=definition,
        default_stage_ownership_mode=StageOwnershipMode.paired,
        ceiling="needs_stages",
        at_cap=AtCap.agent_review,
    )
    explicit = machine.effective_stage_ownership_mode(
        "needs_understanding",
        {"needs_understanding": StageOwnershipMode.paired},
        worker_type_definition=definition,
        default_stage_ownership_mode=StageOwnershipMode.paired,
        ceiling="needs_stages",
        at_cap=AtCap.agent_review,
    )
    assert converted is StageOwnershipMode.worker
    assert beyond_ceiling is StageOwnershipMode.paired
    assert explicit is StageOwnershipMode.paired


def test_paired_non_agent_proposal_route_is_user_review() -> None:
    assert (
        machine.parked_proposal_review_route(
            StageOwnershipMode.paired,
            AtCap.agent_review,
        )
        is ProposalReviewRoute.user_review
    )
