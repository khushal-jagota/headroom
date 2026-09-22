"""The Ticket ceiling holder is the stable address and authority for its proposal."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from sqlite3 import Connection
from unittest.mock import AsyncMock

import pytest

from planner.core.authctx import RequestContext
from planner.core.clock import Clock, parse_fake_now
from planner.core.clock import TestClock as MutableClock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.message_delivery import service as message_delivery_service
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
    parked = data.file_current_proposal(
        conn,
        ticket.id,
        body="Success proposal",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=now + 1,
    )
    if parked.ceiling_holder != holder:
        return data.edit_ticket(
            conn,
            ticket.id,
            edit=TicketEdit(ceiling_holder=holder),
            title_max_chars=TITLE_MAX_CHARS,
            principal=OWNER_PRINCIPAL,
            now=now + 2,
        )
    return parked


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
            boundary_hour=5,
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
    assert [row.ticket_id for row in days_data.list_day_tickets(tmp_db, "day_2026-07-04")] == [
        ticket.id
    ]


def test_rejection_rolls_back_feedback_proposal_day_and_claim_together(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'c_atomic_rejection' WHERE id = ?",
        (ticket.id,),
    )

    def fail_day_placement(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("day placement failed")

    monkeypatch.setattr(days_data, "add_day_ticket", fail_day_placement)
    with pytest.raises(RuntimeError, match="day placement failed"):
        data.reject_proposal(
            tmp_db,
            ticket.id,
            message="Keep all rejection writes atomic.",
            principal=OWNER_PRINCIPAL,
            planning_day_id="day_2026-07-04",
            now=20,
        )

    unchanged = data.read_ticket(tmp_db, ticket.id)
    assert unchanged.pending_proposal == ticket.pending_proposal
    assert unchanged.worker_step_claim.value == "none"
    assert revision_feedback.snapshot(tmp_db, ticket.id) is None
    assert days_data.list_day_tickets(tmp_db, "day_2026-07-04") == []


def test_rejection_resolves_the_day_after_awaited_reply_lookup(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'c_boundary_rejection' WHERE id = ?",
        (ticket.id,),
    )
    clock = MutableClock(parse_fake_now("2026-09-22T04:59:00+02:00"))

    async def cross_the_boundary(*_args: object, **_kwargs: object) -> None:
        clock.set(parse_fake_now("2026-09-22T05:01:00+02:00"))

    monkeypatch.setattr(
        message_delivery_service,
        "revision_source_turn",
        cross_the_boundary,
    )
    revised = asyncio.run(
        actions.reject_ticket_proposal(
            AsyncMock(),
            tmp_db,
            ticket.id,
            message="Resolve the Day after the reply lookup.",
            ctx=RequestContext(OWNER_PRINCIPAL),
            clock=clock,
            boundary_hour=5,
        )
    )

    assert revised.pending_proposal is None
    assert [row.ticket_id for row in days_data.list_day_tickets(tmp_db, "day_2026-09-22")] == [
        ticket.id
    ]
    assert days_data.list_day_tickets(tmp_db, "day_2026-09-21") == []


def test_revision_feedback_is_discarded_when_the_ticket_leaves_its_stage(
    tmp_db: Connection,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute("UPDATE tickets SET conversation_id='c_stage_scope' WHERE id=?", (ticket.id,))
    data.reject_proposal(
        tmp_db,
        ticket.id,
        message="Revise only this stage.",
        principal=OWNER_PRINCIPAL,
        planning_day_id="day_2026-07-04",
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


def test_a_ticket_holder_cannot_be_deleted_while_another_ticket_uses_it(
    tmp_db: Connection,
) -> None:
    holder = data.create_ticket(
        tmp_db,
        title="Holder",
        principal=OWNER_PRINCIPAL,
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note=None,
    )
    data.create_ticket(
        tmp_db,
        title="Held",
        principal=Principal(PrincipalKind.ticket, holder.id),
        now=11,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note=None,
    )
    with pytest.raises(PlannerError, match="holds another Ticket ceiling"):
        data.delete_ticket(tmp_db, holder.id, principal=OWNER_PRINCIPAL, now=12)


def test_the_holder_check_and_the_startup_audit_refuse_a_broken_holder(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "proposal-holder.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.execute(
        "INSERT INTO tickets "
        "(id,title,worker_type,employee_backend,stage,ceiling,"
        "field_values,created_at,updated_at) "
        "VALUES ('t_old','Old','coding','codex','needs_brief','needs_brief','{}',1,1)"
    )

    # A row that names no holder takes the owner, which is the column's default.
    assert data.read_ticket(conn, "t_old").ceiling_holder == OWNER_PRINCIPAL
    invalid_holders = (
        "{}",
        '{"kind":"ticket"}',
        '{"id":"t_old"}',
        '{"kind":"owner","id":"chief"}',
        '{"kind":"chief","id":"owner"}',
        '{"kind":"ticket","id":" t_old"}',
        '{"kind":"ticket","id":"t_old "}',
    )
    for invalid_holder in invalid_holders:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE tickets SET ceiling_holder = ? WHERE id = 't_old'",
                (invalid_holder,),
            )
    # A holder that is well formed but names nobody passes the CHECK, so the startup
    # audit is what catches it.
    conn.execute("PRAGMA ignore_check_constraints=ON")
    conn.execute(
        "UPDATE tickets SET ceiling_holder = ? WHERE id = 't_old'",
        ('{"id":"missing","kind":"ticket"}',),
    )
    conn.execute("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(RuntimeError, match="ceiling holder does not exist"):
        data.audit_ticket_registry_integrity(conn)
    conn.close()


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


# --- a move that leaves the holder below the Ticket -----------------------------


def _outcome(conn: Connection, title: str, clock: Clock) -> str:
    return sprints_data.create_item(conn, title=title, project_id="project_vylo", clock=clock).id


def _park_under(conn: Connection, outcome_id: str, *, now: int = 10) -> Ticket:
    """A Ticket under an Outcome, with a proposal parked for that same Outcome."""
    holder = Principal(PrincipalKind.sprint_item, outcome_id)
    ticket = data.create_ticket(
        conn,
        title="Addressed to its Outcome",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Agreed kickoff",
        stated_ceiling="needs_success_condition",
        sprint_item_id=outcome_id,
        stated_holder=holder,
    )
    return data.file_current_proposal(
        conn,
        ticket.id,
        body="Success proposal",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=now + 1,
    )


def test_a_move_puts_a_stranded_proposal_back_in_the_user_review(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    """The whole complaint, end to end: parked, moved, and nobody coming."""
    left = _outcome(tmp_db, "Inbox triage", fake_clock)
    joined = _outcome(tmp_db, "Sprint hygiene", fake_clock)
    ticket = _park_under(tmp_db, left)
    day_id = "day_2026-09-21"
    days_data.add_day_ticket(tmp_db, day_id, ticket.id, 20)
    assert views.review_view(tmp_db, day_id=day_id)["items"] == []

    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(sprint_item_id=joined),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=30,
    )

    moved = data.read_ticket(tmp_db, ticket.id)
    assert moved.ceiling_holder == OWNER_PRINCIPAL
    assert moved.pending_proposal == ticket.pending_proposal
    assert "Inbox triage" in moved.guidance and "Sprint hygiene" in moved.guidance
    review = views.review_view(tmp_db, day_id=day_id)
    assert [item["ticket_id"] for item in review["items"]] == [ticket.id]


def test_the_outcome_collection_doors_return_a_stranded_ceiling_too(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    left = _outcome(tmp_db, "Inbox triage", fake_clock)
    joined = _outcome(tmp_db, "Sprint hygiene", fake_clock)

    classified = _park_under(tmp_db, left)
    data.classify_ticket(
        tmp_db, classified.id, sprint_item_id=joined, principal=OWNER_PRINCIPAL, now=30
    )
    assert data.read_ticket(tmp_db, classified.id).ceiling_holder == OWNER_PRINCIPAL

    unclassified = _park_under(tmp_db, left, now=40)
    data.unclassify_ticket(
        tmp_db, unclassified.id, sprint_item_id=left, principal=OWNER_PRINCIPAL, now=50
    )
    emptied = data.read_ticket(tmp_db, unclassified.id)
    assert emptied.ceiling_holder == OWNER_PRINCIPAL
    assert "moved out of **Inbox triage**" in emptied.guidance


def test_a_move_leaves_a_holder_that_still_stands_above_alone(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    """Khushal stands above every Outcome, so no move can strand him."""
    left = _outcome(tmp_db, "Inbox triage", fake_clock)
    joined = _outcome(tmp_db, "Sprint hygiene", fake_clock)
    ticket = data.create_ticket(
        tmp_db,
        title="Held by the user throughout",
        principal=OWNER_PRINCIPAL,
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Agreed kickoff",
        stated_ceiling="needs_success_condition",
        sprint_item_id=left,
    )

    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(sprint_item_id=joined),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=20,
    )

    moved = data.read_ticket(tmp_db, ticket.id)
    assert moved.ceiling_holder == OWNER_PRINCIPAL
    assert moved.guidance == ""


def test_only_a_move_returns_the_ceiling_not_any_other_edit(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    """A Ticket already stranded stays stranded until something moves it."""
    left = _outcome(tmp_db, "Inbox triage", fake_clock)
    ticket = _park_under(tmp_db, left)
    tmp_db.execute("UPDATE tickets SET sprint_item_id = NULL WHERE id = ?", (ticket.id,))

    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(title="Renamed, not moved"),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=30,
    )

    unchanged = data.read_ticket(tmp_db, ticket.id)
    assert unchanged.ceiling_holder == Principal(PrincipalKind.sprint_item, left)
    assert unchanged.guidance == ""
