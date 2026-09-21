"""The Ticket ceiling holder is the stable address and authority for its proposal."""

from __future__ import annotations

import asyncio
from sqlite3 import Connection
from unittest.mock import AsyncMock

import pytest

from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.sprints import data as sprints_data
from planner.tickets import actions, data, revision_feedback, views
from planner.tickets.contracts import TITLE_MAX_CHARS, Ticket, TicketEdit


def _park(
    conn: Connection,
    holder: Principal,
    now: int = 10,
    *,
    worker_type: str = "coding",
    stated_ceiling: str = "needs_success_condition",
) -> Ticket:
    ticket = data.create_ticket(
        conn,
        title=f"Proposal for {holder.kind.value}",
        principal=holder,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type=worker_type,
        kickoff_note="Agreed kickoff",
        stated_ceiling=stated_ceiling,
    )
    return data.file_current_proposal(
        conn,
        ticket.id,
        body="Success proposal",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=now + 1,
    )


def test_canonical_proposal_writer_accepts_only_the_ticket_own_worker(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    parent = data.create_ticket(
        tmp_db,
        title="Parent",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Parent",
    )
    other = data.create_ticket(
        tmp_db,
        title="Other",
        principal=OWNER_PRINCIPAL,
        now=2,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Other",
    )
    item = sprints_data.create_item(
        tmp_db, title="Supervisor", project_id="project_vylo", clock=fake_clock
    )
    target = data.create_ticket(
        tmp_db,
        title="Target",
        principal=Principal(PrincipalKind.ticket, parent.id),
        now=3,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Target",
        stated_ceiling="needs_success_condition",
    )
    forbidden_principals = (
        Principal(PrincipalKind.sprint_item, item.id),
        Principal(PrincipalKind.ticket, parent.id),
        Principal(PrincipalKind.ticket, other.id),
    )
    for principal in forbidden_principals:
        with pytest.raises(PlannerError) as forbidden:
            data.file_current_proposal(
                tmp_db,
                target.id,
                body="Not mine",
                principal=principal,
                now=4,
            )
        assert forbidden.value.code is ErrorCode.agent_forbidden

    parked = data.file_current_proposal(
        tmp_db,
        target.id,
        body="Mine",
        principal=Principal(PrincipalKind.ticket, target.id),
        now=5,
    )
    assert parked.pending_proposal is not None
    assert parked.pending_proposal.body == "Mine"


def test_anyone_above_the_ticket_decides_and_approval_requires_next_holder(
    tmp_db: Connection,
) -> None:
    """Deciding is the one rule. The holder the proposal is addressed to grants nothing."""
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    approved = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="success_condition",
        principal=CHIEF_PRINCIPAL,
        now=12,
        next_ceiling="needs_what_changes",
        next_holder=CHIEF_PRINCIPAL,
    )
    assert approved.ceiling_holder == CHIEF_PRINCIPAL


def test_ticket_cannot_hold_or_decide_its_own_ceiling(tmp_db: Connection) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    self_principal = Principal(PrincipalKind.ticket, ticket.id)
    with pytest.raises(PlannerError, match="cannot hold its own ceiling"):
        data.accept_proposal(
            tmp_db,
            ticket.id,
            field="success_condition",
            principal=OWNER_PRINCIPAL,
            now=12,
            next_ceiling="needs_what_changes",
            next_holder=self_principal,
        )

    # And it could not decide one if the column somehow said it held it. Nothing is
    # below itself, so a Ticket never stands above the Ticket it is.
    with pytest.raises(PlannerError) as forbidden:
        data.accept_proposal(
            tmp_db,
            ticket.id,
            field="success_condition",
            principal=self_principal,
            now=12,
            next_ceiling="needs_what_changes",
            next_holder=OWNER_PRINCIPAL,
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden


def test_scope_cannot_retarget_a_pending_proposal(tmp_db: Connection) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    with pytest.raises(PlannerError, match="proposal is pending"):
        data.edit_ticket(
            tmp_db,
            ticket.id,
            edit=TicketEdit(ceiling="needs_plan"),
            title_max_chars=200,
            principal=CHIEF_PRINCIPAL,
            now=12,
        )
    unchanged = data.read_ticket(tmp_db, ticket.id)
    assert unchanged.ceiling_holder == OWNER_PRINCIPAL
    assert unchanged.pending_proposal == ticket.pending_proposal


def test_review_lists_only_owner_addressed_proposals(tmp_db: Connection) -> None:
    owner_ticket = _park(tmp_db, OWNER_PRINCIPAL, 10)
    chief_ticket = _park(tmp_db, CHIEF_PRINCIPAL, 20)
    day_id = "day_2026-09-15"
    days_data.add_day_ticket(tmp_db, day_id, owner_ticket.id, 30)
    days_data.add_day_ticket(tmp_db, day_id, chief_ticket.id, 30)

    review = views.review_view(tmp_db, day_id=day_id)
    assert [item["ticket_id"] for item in review["items"]] == [owner_ticket.id]


def test_revision_stores_exact_attributed_feedback_without_mutating_guidance_and_rests(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='c_worker', guidance='Keep this.' WHERE id=?",
        (ticket.id,),
    )

    revised = asyncio.run(
        actions.reject_ticket_proposal(
            AsyncMock(),
            tmp_db,
            ticket.id,
            message="  Preserve exact spacing.  ",
            ctx=RequestContext(OWNER_PRINCIPAL),
            clock=fake_clock,
        )
    )

    assert revised.pending_proposal is None
    assert revised.guidance == "Keep this."
    assert revised.ticket_status.value == "empty"
    feedback = revision_feedback.snapshot(tmp_db, ticket.id)
    assert feedback is not None
    assert feedback.stage == ticket.stage
    assert feedback.items[0].sender == OWNER_PRINCIPAL
    assert feedback.items[0].message == "  Preserve exact spacing.  "


def test_revision_feedback_is_discarded_when_the_ticket_leaves_its_stage(
    tmp_db: Connection,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='c_stage_scope' WHERE id=?", (ticket.id,)
    )
    data.reject_proposal(
        tmp_db,
        ticket.id,
        message="Revise only this stage.",
        principal=OWNER_PRINCIPAL,
        now=20,
    )
    data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="Revised success",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=21,
    )
    data.accept_proposal(
        tmp_db,
        ticket.id,
        field="success_condition",
        principal=OWNER_PRINCIPAL,
        now=22,
        next_ceiling="needs_what_changes",
        next_holder=OWNER_PRINCIPAL,
    )

    assert revision_feedback.snapshot(tmp_db, ticket.id) is None
    assert (
        tmp_db.execute(
            "SELECT 1 FROM ticket_revision_feedback WHERE ticket_id=?", (ticket.id,)
        ).fetchone()
        is None
    )


def test_setting_the_ceiling_leaves_the_holder_alone(tmp_db: Connection) -> None:
    """How far a Ticket may go and who is asked are set separately."""
    ticket = data.create_ticket(
        tmp_db,
        title="Chief-held",
        principal=CHIEF_PRINCIPAL,
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Kickoff",
        stated_ceiling="needs_success_condition",
    )
    assert ticket.ceiling_holder == CHIEF_PRINCIPAL

    moved = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(ceiling="needs_plan"),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=11,
    )
    assert moved.ceiling == "needs_plan"
    assert moved.ceiling_holder == CHIEF_PRINCIPAL


def test_the_holder_moves_while_a_proposal_is_parked(tmp_db: Connection) -> None:
    ticket = _park(tmp_db, CHIEF_PRINCIPAL)
    assert ticket.pending_proposal is not None

    handed = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
        title_max_chars=TITLE_MAX_CHARS,
        principal=CHIEF_PRINCIPAL,
        now=12,
    )
    assert handed.ceiling_holder == OWNER_PRINCIPAL
    assert handed.ceiling == ticket.ceiling
    assert handed.pending_proposal == ticket.pending_proposal


def test_only_the_current_holder_or_the_user_moves_the_holder(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    item = sprints_data.create_item(
        tmp_db, title="Bystander", project_id="project_vylo", clock=fake_clock
    )
    ticket = _park(tmp_db, CHIEF_PRINCIPAL)

    with pytest.raises(PlannerError) as forbidden:
        data.edit_ticket(
            tmp_db,
            ticket.id,
            edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
            title_max_chars=TITLE_MAX_CHARS,
            principal=Principal(PrincipalKind.sprint_item, item.id),
            now=12,
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden
    assert data.read_ticket(tmp_db, ticket.id).ceiling_holder == CHIEF_PRINCIPAL

    taken = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=13,
    )
    assert taken.ceiling_holder == OWNER_PRINCIPAL


def test_creation_names_a_holder_the_creator_does_not_hold(tmp_db: Connection) -> None:
    """The Chief opens a Ticket that Khushal holds, without holding it first."""
    ticket = data.create_ticket(
        tmp_db,
        title="For the user to review",
        principal=CHIEF_PRINCIPAL,
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Kickoff",
        stated_ceiling="needs_success_condition",
        stated_holder=OWNER_PRINCIPAL,
    )
    assert ticket.ceiling_holder == OWNER_PRINCIPAL


def test_a_worker_cannot_move_its_own_ticket_holder(tmp_db: Connection) -> None:
    """Re-addressing a parked proposal is a write from above, not the Ticket's own record."""
    ticket = _park(tmp_db, CHIEF_PRINCIPAL)
    with pytest.raises(PlannerError) as forbidden:
        data.edit_ticket(
            tmp_db,
            ticket.id,
            edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
            title_max_chars=TITLE_MAX_CHARS,
            principal=Principal(PrincipalKind.ticket, ticket.id),
            now=12,
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden
    assert data.read_ticket(tmp_db, ticket.id).ceiling_holder == CHIEF_PRINCIPAL


def test_holding_a_ceiling_grants_a_ticket_nothing(tmp_db: Connection) -> None:
    """A holder is an address. Being one gives a Ticket no reach it did not have."""
    parent = data.create_ticket(
        tmp_db,
        title="Parent",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Parent",
    )
    holder = Principal(PrincipalKind.ticket, parent.id)
    child = _park(tmp_db, holder, now=10)
    assert child.ceiling_holder == holder

    with pytest.raises(PlannerError) as forbidden:
        data.edit_ticket(
            tmp_db,
            child.id,
            edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
            title_max_chars=TITLE_MAX_CHARS,
            principal=holder,
            now=12,
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden
    assert data.read_ticket(tmp_db, child.id).ceiling_holder == holder
