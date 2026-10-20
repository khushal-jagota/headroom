"""The Ticket ceiling holder is the stable address and authority for its proposal."""

from __future__ import annotations

import hashlib
import sqlite3
from sqlite3 import Connection

import pytest

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
from planner.tickets import data, revision_feedback, views
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
        brief="Agreed kickoff",
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




def test_review_lists_only_owner_addressed_proposals(tmp_db: Connection) -> None:
    owner_ticket = _park(tmp_db, OWNER_PRINCIPAL, 10)
    chief_ticket = _park(tmp_db, CHIEF_PRINCIPAL, 20)
    day_id = "day_2026-09-15"
    days_data.add_day_ticket(tmp_db, day_id, owner_ticket.id, 30)
    days_data.add_day_ticket(tmp_db, day_id, chief_ticket.id, 30)

    review = views.review_view(tmp_db)
    assert [item["ticket_id"] for item in review["items"]] == [owner_ticket.id]




def test_rejection_rolls_back_feedback_proposal_and_claim_together(
    tmp_db: Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'c_atomic_rejection' WHERE id = ?",
        (ticket.id,),
    )

    def fail_attention_capture(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("attention capture failed")

    monkeypatch.setattr(data, "capture_ticket_attention", fail_attention_capture)
    with pytest.raises(RuntimeError, match="attention capture failed"):
        data.reject_proposal(
            tmp_db,
            ticket.id,
            message="Keep all rejection writes atomic.",
            principal=OWNER_PRINCIPAL,
            now=20,
        )

    unchanged = data.read_ticket(tmp_db, ticket.id)
    assert unchanged.pending_proposal == ticket.pending_proposal
    assert unchanged.worker_step_claim.value == "none"
    assert revision_feedback.snapshot(tmp_db, ticket.id) is None
    assert days_data.list_day_tickets(tmp_db, "day_2026-07-04") == []






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
        brief=None,
    )
    data.create_ticket(
        tmp_db,
        title="Held",
        principal=Principal(PrincipalKind.ticket, holder.id),
        now=11,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief=None,
    )
    with pytest.raises(PlannerError, match="holds another Ticket ceiling"):
        data.delete_ticket(tmp_db, holder.id, principal=OWNER_PRINCIPAL, now=12)






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
        brief="Agreed kickoff",
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
    assert views.review_view(tmp_db)["items"] == []

    tmp_db.execute(
        "UPDATE tickets SET guidance=? WHERE id=?", ("Historical move text λ", ticket.id)
    )
    before = hashlib.sha256(tmp_db.execute(
        "SELECT CAST(guidance AS BLOB) FROM tickets WHERE id=?", (ticket.id,)
    ).fetchone()[0]).hexdigest()

    def deny_guidance(
        action: int, table: str | None, column: str | None, _db: str | None, _trigger: str | None,
    ) -> int:
        if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_UPDATE) and (
            table, column
        ) == ("tickets", "guidance"):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    tmp_db.set_authorizer(deny_guidance)
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
    assert moved.updated_at == 30
    review = views.review_view(tmp_db)
    assert [item["ticket_id"] for item in review["items"]] == [ticket.id]
    tmp_db.set_authorizer(None)
    after = hashlib.sha256(tmp_db.execute(
        "SELECT CAST(guidance AS BLOB) FROM tickets WHERE id=?", (ticket.id,)
    ).fetchone()[0]).hexdigest()
    assert before == after


def test_a_backlogged_ticket_is_excluded_from_the_review_queue(tmp_db: Connection) -> None:
    visible = _park(tmp_db, OWNER_PRINCIPAL, now=10)
    backlogged = _park(tmp_db, OWNER_PRINCIPAL, now=20)
    data.set_ticket_backlog(
        tmp_db, backlogged.id, is_backlogged=True, principal=OWNER_PRINCIPAL, now=21
    )

    ticket_ids = [item["ticket_id"] for item in views.review_view(tmp_db)["items"]]
    assert visible.id in ticket_ids
    assert backlogged.id not in ticket_ids
