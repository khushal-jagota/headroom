"""Acceptance items 2, 3, 4, 5, 6, 7, 8, 13, 36 for the tickets engine (SPEC §4.4).

Drives everything through planner.tickets.data against a real temp SQLite DB via
the shared conftest fixtures. No mocks. Assertions pin the frozen states, event
payloads, event order, and error codes from the T04 plan.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest
from tests.support.principals import OWNER_PRINCIPAL, TEST_TICKET_PRINCIPAL, ticket_principal
from tests.support.probe import (
    install_probe_registry,
    seed_probe_worker_type,
    uninstall_probe_registry,
)
from tests.support.ticket_progress import advance_ticket

from planner.core import ticket_blocks
from planner.core.contracts import Principal, PrincipalKind
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime import worker_step_readiness
from planner.tickets import actions, data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    TicketEdit,
    TicketStatus,
)

if TYPE_CHECKING:
    from sqlite3 import Connection

    from planner.core.clock import TestClock
    from planner.core.config import Config
    from planner.tickets.contracts import Ticket


@pytest.fixture
def probe_runtime(tmp_db: Connection) -> Iterator[None]:
    seed_probe_worker_type(tmp_db)
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


def _create(conn: Connection, cfg: Config, clock: TestClock, **kw: Any) -> Ticket:
    settle_kickoff = kw.pop("settle_kickoff", True)
    ticket = data.create_ticket(
        conn,
        worker_type=kw.pop("worker_type", "coding"),
        title=kw.pop("title", "Test ticket"),
        principal=OWNER_PRINCIPAL,
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        brief=kw.pop("brief", "Agreed brief."),
        **kw,
    )
    if not settle_kickoff:
        return ticket
    ticket = data.accept_proposal(
        conn,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=clock.now_unix(),
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    return ticket


def _scope(conn: Connection, t: Ticket, ceiling: str, clock: TestClock) -> Ticket:
    return data.edit_ticket(
        conn,
        t.id,
        edit=TicketEdit(ceiling=ceiling),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=clock.now_unix(),
    )


def _claim_ready_worker_step(conn: Connection, ticket_id: str, *, now: int) -> Ticket | None:
    return data.claim_ticket_for_worker_step(
        conn,
        ticket_id,
        readiness_check=worker_step_readiness.is_ready_for_worker_step,
        now=now,
    )


def _ticket_count(conn: Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0])


def _ticket_row(conn: Connection, ticket_id: str) -> tuple[object, ...]:
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    assert row is not None
    return tuple(row)


def test_accept_kickoff_field_advances_to_success_and_leaves_title_independent(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    t = _create(
        tmp_db,
        cfg,
        fake_clock,
        title="Proposed title",
        brief="proposed note",
        settle_kickoff=False,
    )

    renamed = data.edit_ticket(
        tmp_db,
        t.id,
        edit=TicketEdit(title="Approved title"),
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
    )
    assert renamed.stage == "needs_brief"
    assert renamed.pending_proposal is not None

    settled = data.accept_proposal(
        tmp_db,
        t.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
        edited_body="approved note",
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )

    assert settled.stage == "needs_success_condition"
    assert settled.ticket_status is TicketStatus.empty
    assert settled.title == "Approved title"
    assert settled.field_values.get("brief") == "approved note"
    assert settled.pending_proposal is None
    assert (settled.pending_proposal is not None) is False


def test_a02_gating_chain_one_state_per_accept(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_consequences", fake_clock)

    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="success body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_what_changes"
    assert t.field_values.get("success_condition") == "success body"
    assert t.pending_proposal is None

    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="approach body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_plan"
    assert t.field_values.get("what_changes") == "approach body"

    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="plan body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_implementation"
    assert t.field_values.get("plan") == "plan body"

    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="implementation body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_consequences"
    assert t.field_values.get("implementation") == "implementation body"
    assert t.pending_proposal is None


def test_a03_ceiling_auto_accept_until_cap_then_pending(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t, "needs_plan", fake_clock)

    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="s",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_what_changes"
    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="a",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_plan"

    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="plan body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_plan"
    assert t.field_values.get("plan") is None
    assert t.pending_proposal is not None
    assert t.pending_proposal.body == "plan body"
    assert t.pending_proposal.proposed_by == "worker"
    assert (t.pending_proposal is not None) is True

    assert t.ticket_status is TicketStatus.awaiting_approval


def test_a06_edit_accept_stores_edited_text(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="draft body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success_condition",
        principal=OWNER_PRINCIPAL,
        now=now,
        edited_body="edited body exactly",
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t.field_values.get("success_condition") == "edited body exactly"
    assert t.pending_proposal is None

    assert t.stage == "needs_what_changes"
    assert t.ceiling == "needs_what_changes"


def test_a07_closeout_routing(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()

    # Ceiling stops exactly at needs_closeout: implementation auto-accepts up to
    # needs_closeout, and closeout then routes to done through the ordinary
    # accept machinery (no special-cased manual review step).
    t1 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t1, "needs_consequences", fake_clock)
    for _field, body in [
        ("success_condition", "s"),
        ("what_changes", "a"),
        ("plan", "p"),
        ("implementation", "i"),
    ]:
        t1 = data.file_current_proposal(
            tmp_db,
            t1.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t1.id),
            now=now,
        )
    assert t1.stage == "needs_consequences"
    t1 = data.file_current_proposal(
        tmp_db,
        t1.id,
        body="c",
        principal=Principal(PrincipalKind.ticket, t1.id),
        now=now,
    )
    assert t1.stage == "needs_consequences"
    assert t1.pending_proposal is not None
    assert t1.field_values.get("consequences") is None

    t1 = data.accept_proposal(
        tmp_db,
        t1.id,
        field="consequences",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t1.stage == "done"
    assert t1.field_values.get("consequences") == "c"
    assert t1.ceiling == "done"

    # Ceiling at done from the start: every field, including closeout, auto-accepts
    # straight through to done.
    t2 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t2, "done", fake_clock)
    for _field, body in [
        ("success_condition", "s"),
        ("what_changes", "a"),
        ("plan", "p"),
        ("implementation", "i"),
        ("consequences", "c"),
    ]:
        t2 = data.file_current_proposal(
            tmp_db,
            t2.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t2.id),
            now=now,
        )
    assert t2.stage == "done"
    assert all(
        t2.field_values.get(field) == body
        for field, body in [
            ("success_condition", "s"),
            ("what_changes", "a"),
            ("plan", "p"),
            ("implementation", "i"),
            ("consequences", "c"),
        ]
    )

    # Ceiling below needs_closeout: the closeout proposal stays pending until the
    # ceiling is raised and it is explicitly accepted.
    t3 = _create(tmp_db, cfg, fake_clock)
    _scope(tmp_db, t3, "needs_implementation", fake_clock)
    for _field, body in [("success_condition", "s"), ("what_changes", "a"), ("plan", "p")]:
        t3 = data.file_current_proposal(
            tmp_db,
            t3.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t3.id),
            now=now,
        )
    assert t3.stage == "needs_implementation"
    t3 = data.file_current_proposal(
        tmp_db,
        t3.id,
        body="i",
        principal=Principal(PrincipalKind.ticket, t3.id),
        now=now,
    )
    assert t3.stage == "needs_implementation"
    assert t3.pending_proposal is not None

    t3 = data.accept_proposal(
        tmp_db,
        t3.id,
        field="implementation",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling="done",
        next_holder=OWNER_PRINCIPAL,
    )
    assert t3.stage == "needs_consequences"


def test_a36_onward_scope(tmp_db: Connection, cfg: Config, fake_clock: TestClock) -> None:
    now = fake_clock.now_unix()

    t = _create(tmp_db, cfg, fake_clock)
    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="body",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.pending_proposal is not None
    row_before = _ticket_row(tmp_db, t.id)

    with pytest.raises(PlannerError) as e_missing_ceiling:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success_condition",
            principal=OWNER_PRINCIPAL,
            now=now,
            next_ceiling=None,
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_missing_ceiling.value.code is ErrorCode.scope_missing

    t = data.read_ticket(tmp_db, t.id)
    assert t.stage == "needs_success_condition"
    assert t.field_values.get("success_condition") is None
    assert t.pending_proposal is not None
    assert t.pending_proposal.body == "body"
    assert t.ceiling == "needs_success_condition"
    assert _ticket_row(tmp_db, t.id) == row_before

    with pytest.raises(PlannerError) as e_before:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success_condition",
            principal=OWNER_PRINCIPAL,
            now=now,
            next_ceiling="needs_success_condition",
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_before.value.code is ErrorCode.scope_invalid
    with pytest.raises(PlannerError) as e_unknown:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success_condition",
            principal=OWNER_PRINCIPAL,
            now=now,
            next_ceiling="needs_nothing",
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_unknown.value.code is ErrorCode.scope_invalid
    assert data.read_ticket(tmp_db, t.id).stage == "needs_success_condition"
    assert _ticket_row(tmp_db, t.id) == row_before

    with pytest.raises(PlannerError) as e_agent:
        data.accept_proposal(
            tmp_db,
            t.id,
            field="success_condition",
            principal=TEST_TICKET_PRINCIPAL,
            now=now,
            next_ceiling=NO_FURTHER,
            next_holder=OWNER_PRINCIPAL,
        )
    assert e_agent.value.code is ErrorCode.agent_forbidden
    assert _ticket_row(tmp_db, t.id) == row_before

    t = data.accept_proposal(
        tmp_db,
        t.id,
        field="success_condition",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t.stage == "needs_what_changes"
    assert t.ceiling == "needs_what_changes"
    # The Ticket sits at its ceiling, so its next answer parks for approval instead of
    # settling the Stage.
    t = data.file_current_proposal(
        tmp_db,
        t.id,
        body="x",
        principal=Principal(PrincipalKind.ticket, t.id),
        now=now,
    )
    assert t.stage == "needs_what_changes"
    assert t.pending_proposal is not None
    assert t.ticket_status is TicketStatus.awaiting_approval

    t2 = _create(tmp_db, cfg, fake_clock)
    t2 = data.file_current_proposal(
        tmp_db,
        t2.id,
        body="body",
        principal=Principal(PrincipalKind.ticket, t2.id),
        now=now,
    )
    t2 = data.accept_proposal(
        tmp_db,
        t2.id,
        field="success_condition",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling=NO_FURTHER,
        next_holder=OWNER_PRINCIPAL,
    )
    assert t2.stage == "needs_what_changes"
    t2 = data.file_current_proposal(
        tmp_db,
        t2.id,
        body="draft",
        principal=Principal(PrincipalKind.ticket, t2.id),
        now=now,
    )
    assert t2.pending_proposal is not None
    assert t2.stage == "needs_what_changes"

    t3 = _create(tmp_db, cfg, fake_clock)
    t3 = data.file_current_proposal(
        tmp_db,
        t3.id,
        body="body",
        principal=Principal(PrincipalKind.ticket, t3.id),
        now=now,
    )
    t3 = data.accept_proposal(
        tmp_db,
        t3.id,
        field="success_condition",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling="needs_plan",
        next_holder=OWNER_PRINCIPAL,
    )
    assert t3.ceiling == "needs_plan"
    t3 = data.file_current_proposal(
        tmp_db,
        t3.id,
        body="a",
        principal=Principal(PrincipalKind.ticket, t3.id),
        now=now,
    )
    assert t3.stage == "needs_plan"
    assert t3.ceiling == "needs_plan"

    t4 = _create(tmp_db, cfg, fake_clock)
    t4 = _scope(tmp_db, t4, "done", fake_clock)
    ceiling_before = t4.ceiling
    for _field, body in [
        ("success_condition", "s"),
        ("what_changes", "a"),
        ("plan", "p"),
        ("implementation", "i"),
        ("consequences", "c"),
    ]:
        t4 = data.file_current_proposal(
            tmp_db,
            t4.id,
            body=body,
            principal=Principal(PrincipalKind.ticket, t4.id),
            now=now,
        )
    assert t4.stage == "done"
    assert t4.ceiling == ceiling_before


# --- blocked: empty's stand-in while a live blocker exists ----------------------


def _block(conn: Connection, *, blocker_id: str, target_id: str, now: int) -> None:
    actions.add_ticket_block(conn, blocker_id, target_id, now=now)


def _blocked_tickets(conn: Connection, ticket_id: str) -> list[str]:
    return [
        str(row["blocked_ticket_id"])
        for row in conn.execute(
            "SELECT blocked_ticket_id FROM ticket_blocks "
            "WHERE blocking_ticket_id = ? ORDER BY blocked_ticket_id",
            (ticket_id,),
        ).fetchall()
    ]


def _created_with_blockers(
    conn: Connection,
    cfg: Config,
    clock: TestClock,
    *,
    title: str,
    blocker_ids: list[str],
) -> Ticket:
    """Create a dependent Ticket the way the incident did.

    With its blockers and a ceiling that accepts the Brief inside the create itself,
    so creation is the only write the Ticket receives. No Day membership is required."""
    return _create(
        conn,
        cfg,
        clock,
        title=title,
        settle_kickoff=False,
        stated_ceiling="needs_success_condition",
        blocked_by_ticket_ids=blocker_ids,
    )


def test_a_ticket_created_with_a_live_blocker_never_starts_until_it_clears(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    created = _created_with_blockers(
        tmp_db, cfg, fake_clock, title="Blocked at birth", blocker_ids=[blocker.id]
    )

    assert created.stage == "needs_success_condition"
    assert created.ticket_status is TicketStatus.blocked
    assert _claim_ready_worker_step(tmp_db, created.id, now=now) is None

    advance_ticket(tmp_db, blocker.id, new_stage="done", principal=OWNER_PRINCIPAL, now=now)

    assert data.read_ticket(tmp_db, created.id).ticket_status is TicketStatus.empty
    claimed = _claim_ready_worker_step(tmp_db, created.id, now=now + 1)
    assert claimed is not None
    assert claimed.ticket_status is TicketStatus.agent


def test_every_creation_blocker_holds_until_the_last_one_is_done(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    now = fake_clock.now_unix()
    blockers = [_create(tmp_db, cfg, fake_clock, title=f"Blocker {index}").id for index in range(4)]
    created = _created_with_blockers(
        tmp_db, cfg, fake_clock, title="Blocked four times", blocker_ids=blockers
    )

    assert created.ticket_status is TicketStatus.blocked
    # Every named blocker, not just the last one the create happened to write.
    assert [_blocked_tickets(tmp_db, blocker_id) for blocker_id in blockers] == [
        [created.id] for _ in blockers
    ]
    assert _claim_ready_worker_step(tmp_db, created.id, now=now) is None

    # Cleared in an order other than the one they were named in, so no single blocker
    # can be the one that happens to hold it.
    clearing_order = [blockers[2], blockers[0], blockers[3], blockers[1]]
    for blocker_id in clearing_order[:-1]:
        advance_ticket(tmp_db, blocker_id, new_stage="done", principal=OWNER_PRINCIPAL, now=now)
        assert data.read_ticket(tmp_db, created.id).ticket_status is TicketStatus.blocked
        assert _claim_ready_worker_step(tmp_db, created.id, now=now) is None

    advance_ticket(tmp_db, clearing_order[-1], new_stage="done", principal=OWNER_PRINCIPAL, now=now)

    claimed = _claim_ready_worker_step(tmp_db, created.id, now=now + 1)
    assert claimed is not None
    assert claimed.ticket_status is TicketStatus.agent


def test_a_parked_brief_outranks_a_creation_blocker_until_it_is_settled(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # The other path through creation: the Brief parks instead of being accepted inside
    # the create. A parked proposal outranks a blocker, so the Ticket reads as waiting on
    # the user first and as blocked from the moment that is settled.
    now = fake_clock.now_unix()
    blocker = _create(tmp_db, cfg, fake_clock, title="Blocker")
    created = _create(
        tmp_db,
        cfg,
        fake_clock,
        title="Parked and blocked",
        settle_kickoff=False,
        blocked_by_ticket_ids=[blocker.id],
    )

    assert created.stage == "needs_brief"
    assert created.ticket_status is TicketStatus.awaiting_approval

    settled = data.accept_proposal(
        tmp_db,
        created.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=now,
        next_ceiling="needs_success_condition",
        next_holder=OWNER_PRINCIPAL,
    )

    assert settled.ticket_status is TicketStatus.blocked
    assert _claim_ready_worker_step(tmp_db, created.id, now=now) is None


def test_a_failed_creation_block_rolls_the_whole_create_back(
    tmp_db: Connection,
    cfg: Config,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The Ticket row and its blocks are one transaction. A second reader
    # must never find the Ticket without the blockers it was created with, so a failure
    # partway through the blocks has to take the Ticket with it.
    first = _create(tmp_db, cfg, fake_clock, title="First blocker")
    second = _create(tmp_db, cfg, fake_clock, title="Second blocker")
    tickets_before = _ticket_count(tmp_db)
    write_block = ticket_blocks.add_ticket_block
    written: list[str] = []

    def fail_on_the_second(
        conn: Connection, blocking_ticket_id: str, blocked_ticket_id: str, now: int
    ) -> None:
        written.append(blocking_ticket_id)
        if len(written) == 2:
            raise RuntimeError("block write failed mid-create")
        write_block(conn, blocking_ticket_id, blocked_ticket_id, now)

    monkeypatch.setattr(ticket_blocks, "add_ticket_block", fail_on_the_second)
    with pytest.raises(RuntimeError, match="mid-create"):
        _created_with_blockers(
            tmp_db, cfg, fake_clock, title="Never existed", blocker_ids=[first.id, second.id]
        )

    assert written == [first.id, second.id]
    assert _ticket_count(tmp_db) == tickets_before
    assert _blocked_tickets(tmp_db, first.id) == []


def test_a_claim_release_does_not_fire_once_the_ticket_has_moved_on(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    # The status alone is not enough to identify a claim: a Ticket can leave `agent` and
    # come back to it legitimately, and a late release must not erase that second arrival.
    now = fake_clock.now_unix()
    t = _create(tmp_db, cfg, fake_clock)
    claimed = _claim_ready_worker_step(tmp_db, t.id, now=now)
    assert claimed is not None

    assert data.release_worker_step_claim(
        tmp_db,
        t.id,
        expected_claim=claimed.worker_step_claim,
        expected_claim_revision=claimed.worker_step_claim_revision,
        now=now,
    )
    reclaimed = _claim_ready_worker_step(tmp_db, t.id, now=now + 5)
    assert reclaimed is not None
    assert reclaimed.ticket_status is claimed.ticket_status
    assert reclaimed.worker_step_claim_revision != claimed.worker_step_claim_revision

    assert (
        data.release_worker_step_claim(
            tmp_db,
            t.id,
            expected_claim=claimed.worker_step_claim,
            expected_claim_revision=claimed.worker_step_claim_revision,
            now=now + 6,
        )
        is False
    )
    assert data.read_ticket(tmp_db, t.id).ticket_status is TicketStatus.agent


def test_auto_approval_old_data_and_multi_field_edit(
    tmp_db: Connection, cfg: Config, fake_clock: TestClock
) -> None:
    """Creation and worker auto acceptance differ from old data and edited approvals."""
    now = fake_clock.now_unix()
    ticket = _create(
        tmp_db, cfg, fake_clock, stated_ceiling="needs_what_changes", settle_kickoff=False
    )
    assert ticket.field_approvals["brief"].method == "auto_accept"
    ticket = data.file_current_proposal(
        tmp_db, ticket.id, body="Success", principal=ticket_principal(ticket.id), now=now
    )
    assert ticket.field_approvals["success_condition"].principal is None
    assert ticket.field_approvals["success_condition"].display_label == "Auto-accepted"
    before = _ticket_row(tmp_db, ticket.id)
    with pytest.raises(PlannerError):
        data.edit_ticket(
            tmp_db,
            ticket.id,
            edit=TicketEdit(field_values={"brief": "Changed", "plan": "Not settled"}),
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=now,
        )
    assert _ticket_row(tmp_db, ticket.id) == before
    ticket = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(field_values={"brief": "Changed", "success_condition": "Success"}),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=now,
    )
    assert ticket.field_approvals["brief"].method == "unknown"
    assert ticket.field_approvals["success_condition"].method == "auto_accept"
    # The reverse order catches an accumulator that restores a cleared approval.
    ticket = data.edit_ticket(
        tmp_db,
        ticket.id,
        edit=TicketEdit(field_values={"success_condition": "Changed", "brief": "Changed"}),
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=now,
    )
    assert all(approval.method == "unknown" for approval in ticket.field_approvals.values())
    tmp_db.execute("UPDATE tickets SET field_approvals='{}' WHERE id=?", (ticket.id,))
    old = data.read_ticket(tmp_db, ticket.id)
    assert set(old.field_approvals) == set(old.field_values)
    assert all(
        approval.display_label == "Approval unknown" and approval.principal is None
        for approval in old.field_approvals.values()
    )
