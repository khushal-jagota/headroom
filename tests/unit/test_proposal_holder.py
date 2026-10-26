"""The Ticket ceiling holder is the stable address and authority for its proposal."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from sqlite3 import Connection

import pytest

from planner.core import authority
from planner.core.authority.declarations import STANDS_ABOVE_BY_WORKER_TYPE
from planner.core.clock import Clock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.managers import data as managers_data
from planner.tickets import data, revision_feedback, views
from planner.tickets.contracts import TITLE_MAX_CHARS, ReviewTransferNotice, Ticket, TicketEdit


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
    _park(tmp_db, CHIEF_PRINCIPAL, 20)

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
    held = data.create_ticket(
        tmp_db,
        title="Held",
        principal=Principal(PrincipalKind.ticket, holder.id),
        now=11,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief=None,
    )
    # Omitting an explicit holder retains the ordinary creator default.
    assert held.ceiling_holder == Principal(PrincipalKind.ticket, holder.id)
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


def _manager(conn: Connection, title: str, clock: Clock) -> str:
    return managers_data.create_manager(
        conn, title=title, project_id="project_vylo", clock=clock
    ).id


def _park_under(conn: Connection, manager_id: str, *, now: int = 10) -> Ticket:
    """A Ticket under a Manager, with a proposal parked for that same Manager."""
    holder = Principal(PrincipalKind.manager, manager_id)
    ticket = data.create_ticket(
        conn,
        title="Addressed to its Manager",
        principal=OWNER_PRINCIPAL,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Agreed kickoff",
        stated_ceiling="needs_success_condition",
        manager_id=manager_id,
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
    left = _manager(tmp_db, "Inbox triage", fake_clock)
    joined = _manager(tmp_db, "Sprint hygiene", fake_clock)
    ticket = _park_under(tmp_db, left)
    assert views.review_view(tmp_db)["items"] == []

    tmp_db.execute(
        "UPDATE tickets SET guidance=? WHERE id=?", ("Historical move text λ", ticket.id)
    )
    before = hashlib.sha256(
        tmp_db.execute(
            "SELECT CAST(guidance AS BLOB) FROM tickets WHERE id=?", (ticket.id,)
        ).fetchone()[0]
    ).hexdigest()

    def deny_guidance(
        action: int,
        table: str | None,
        column: str | None,
        _db: str | None,
        _trigger: str | None,
    ) -> int:
        if action in (sqlite3.SQLITE_READ, sqlite3.SQLITE_UPDATE) and (table, column) == (
            "tickets",
            "guidance",
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    tmp_db.set_authorizer(deny_guidance)
    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(manager_id=joined),
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
    after = hashlib.sha256(
        tmp_db.execute(
            "SELECT CAST(guidance AS BLOB) FROM tickets WHERE id=?", (ticket.id,)
        ).fetchone()[0]
    ).hexdigest()
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


def test_invalid_holder_edits_refuse_before_or_after_a_manager_move(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    left = _manager(tmp_db, "Old Manager", fake_clock)
    joined = _manager(tmp_db, "New Manager", fake_clock)
    ticket = _park_under(tmp_db, left)
    before = tuple(tmp_db.iterdump())
    notices: list[ReviewTransferNotice] = []
    for edit in (
        TicketEdit(ceiling_holder=Principal(PrincipalKind.manager, joined)),
        TicketEdit(manager_id=joined, ceiling_holder=ticket.ceiling_holder),
    ):
        with pytest.raises(PlannerError, match="must have authority to decide") as error:
            data.edit_ticket(
                tmp_db,
                ticket.id,
                edit=edit,
                title_max_chars=TITLE_MAX_CHARS,
                principal=OWNER_PRINCIPAL,
                now=30,
                review_transfer_notices=notices,
            )
        assert error.value.code is ErrorCode.validation
        assert tuple(tmp_db.iterdump()) == before
        assert notices == []


@pytest.mark.parametrize("repair", ["edit", "accept"])
def test_historical_invalid_holders_remain_editable_and_repairable(
    tmp_db: Connection, fake_clock: Clock, repair: str
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    stranger = Principal(PrincipalKind.manager, _manager(tmp_db, "Unrelated Manager", fake_clock))
    # Historical relations remain loadable; new explicit writes must not recreate them.
    tmp_db.execute(
        "UPDATE tickets SET ceiling_holder=? WHERE id=?",
        (json.dumps({"kind": stranger.kind.value, "id": stranger.id}), ticket.id),
    )
    historical = data.read_ticket(tmp_db, ticket.id)
    assert historical.ceiling_holder == stranger
    edited = data.edit_ticket(
        tmp_db, ticket.id, edit=TicketEdit(recap="Unrelated authorized update"),
        title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=20,
    )
    assert edited.recap == "Unrelated authorized update"
    assert edited.ceiling_holder == stranger
    unchanged = data.edit_ticket(
        tmp_db, ticket.id, edit=TicketEdit(manager_id=historical.manager_id),
        title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=21,
    )
    assert unchanged.ceiling_holder == stranger
    before = tuple(tmp_db.iterdump())
    with pytest.raises(PlannerError, match="must have authority to decide"):
        data.edit_ticket(
            tmp_db, ticket.id, edit=TicketEdit(ceiling_holder=stranger),
            title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=22,
        )
    assert tuple(tmp_db.iterdump()) == before
    if repair == "edit":
        repaired = data.edit_ticket(
            tmp_db, ticket.id, edit=TicketEdit(ceiling_holder=OWNER_PRINCIPAL),
            title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=23,
        )
        assert repaired.pending_proposal == ticket.pending_proposal
    else:
        repaired = data.accept_proposal(
            tmp_db, ticket.id, field="success_condition", principal=OWNER_PRINCIPAL,
            now=23, next_ceiling="needs_what_changes", next_holder=OWNER_PRINCIPAL,
        )
        assert repaired.field_values["success_condition"] == "Success proposal"
        assert repaired.pending_proposal is None
    assert repaired.ceiling_holder == OWNER_PRINCIPAL


def test_declared_ticket_authority_can_hold_creation_edit_and_acceptance(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = _park(tmp_db, OWNER_PRINCIPAL)
    holder = Principal(PrincipalKind.ticket, reviewer.id)
    # Only this test grants coding Tickets authority. Canonical facts must retain it.
    monkeypatch.setitem(STANDS_ABOVE_BY_WORKER_TYPE, "coding", (authority.ticket("*"),))
    ticket = data.create_ticket(
        tmp_db, title="Declared reviewer", worker_type="coding", brief="Kickoff",
        stated_ceiling="needs_success_condition", stated_holder=holder,
        principal=OWNER_PRINCIPAL, now=20, title_max_chars=TITLE_MAX_CHARS,
    )
    assert ticket.ceiling_holder == holder
    parked = data.file_current_proposal(
        tmp_db, ticket.id, body="Declared review", now=21,
        principal=Principal(PrincipalKind.ticket, ticket.id),
    )
    # Proposal routing is separate from authority admission; explicitly choose the reviewer.
    edited = data.edit_ticket(
        tmp_db, ticket.id, edit=TicketEdit(ceiling_holder=holder),
        title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=22,
    )
    assert edited.ceiling_holder == holder
    assert edited.pending_proposal == parked.pending_proposal
    accepted = data.accept_proposal(
        tmp_db, ticket.id, field="success_condition", principal=OWNER_PRINCIPAL, now=23,
        next_ceiling="needs_what_changes", next_holder=holder,
    )
    assert accepted.ceiling_holder == holder
    assert accepted.field_values["success_condition"] == "Declared review"
