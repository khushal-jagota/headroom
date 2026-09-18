"""The Ticket ceiling holder is the stable address and authority for its proposal."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from unittest.mock import AsyncMock

import pytest
from alembic import command

from planner.conversation.contracts import ConversationTurnReference
from planner.core import db as db_module
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.sprints import data as sprints_data
from planner.tickets import actions, data, views
from planner.tickets.contracts import TITLE_MAX_CHARS, Ticket
from planner.tickets.logic import resolution
from planner.tickets.logic.admission import REVISION_GUIDANCE_MAX_CHARACTERS
from planner.worker_context import revision_feedback
from planner.worker_types.configuration import configured_worker_type_registry


def _park(
    conn: Connection,
    holder: Principal,
    now: int = 10,
    *,
    worker_type: str = "coding",
    stated_ceiling: str = "needs_success",
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
    return data.file_current_proposal_with_recap(
        conn,
        ticket.id,
        body="Success proposal",
        recap="Proposal ready",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=now + 1,
    )


def test_creation_and_auto_accept_preserve_the_creating_principal(
    tmp_db: Connection,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Chief-owned scope",
        principal=CHIEF_PRINCIPAL,
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Kickoff",
        stated_ceiling="needs_approach",
    )
    assert ticket.ceiling_holder == CHIEF_PRINCIPAL

    advanced = data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="Success",
        recap="Moving within scope",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=11,
    )
    assert advanced.stage == "needs_approach"
    assert advanced.pending_proposal is None
    assert advanced.ceiling_holder == CHIEF_PRINCIPAL


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
        stated_ceiling="needs_success",
    )
    forbidden_principals = (
        Principal(PrincipalKind.sprint_item, item.id),
        Principal(PrincipalKind.ticket, parent.id),
        Principal(PrincipalKind.ticket, other.id),
    )
    for principal in forbidden_principals:
        with pytest.raises(PlannerError) as forbidden:
            data.file_current_proposal_with_recap(
                tmp_db,
                target.id,
                body="Not mine",
                recap="Not mine",
                principal=principal,
                now=4,
            )
        assert forbidden.value.code is ErrorCode.agent_forbidden

    parked = data.file_current_proposal_with_recap(
        tmp_db,
        target.id,
        body="Mine",
        recap="Mine",
        principal=Principal(PrincipalKind.ticket, target.id),
        now=5,
    )
    assert parked.pending_proposal is not None
    assert parked.pending_proposal.body == "Mine"


def test_only_holder_or_owner_can_decide_and_approval_requires_next_holder(
    tmp_db: Connection,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    with pytest.raises(PlannerError) as forbidden:
        data.accept_proposal(
            tmp_db,
            ticket.id,
            field="success",
            principal=CHIEF_PRINCIPAL,
            now=12,
            next_ceiling="needs_approach",
            next_holder=CHIEF_PRINCIPAL,
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden

    approved = data.accept_proposal(
        tmp_db,
        ticket.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=12,
        next_ceiling="needs_approach",
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
            field="success",
            principal=OWNER_PRINCIPAL,
            now=12,
            next_ceiling="needs_approach",
            next_holder=self_principal,
        )

    corrupted = replace(ticket, ceiling_holder=self_principal)
    with pytest.raises(PlannerError, match="own worker"):
        resolution.decide_accept(
            corrupted,
            "success",
            self_principal,
            None,
            "needs_approach",
            OWNER_PRINCIPAL,
            worker_type_definition=configured_worker_type_registry().require("coding"),
        )


def test_canonical_approval_writer_requires_an_explicit_next_holder() -> None:
    parameter = inspect.signature(data.accept_proposal).parameters["next_holder"]
    assert parameter.default is inspect.Parameter.empty


def test_scope_cannot_retarget_a_pending_proposal(tmp_db: Connection) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    with pytest.raises(PlannerError, match="proposal is pending"):
        data.set_ceiling(
            tmp_db,
            ticket.id,
            ceiling="needs_plan",
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
        actions.return_ticket_for_revision(
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


def test_revision_rearms_same_user_owned_stage_and_credits_exact_source_turn(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = _park(
        tmp_db,
        CHIEF_PRINCIPAL,
        worker_type="new_worker",
        stated_ceiling="needs_understanding",
    )
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='c_worker' WHERE id=?",
        (ticket.id,),
    )
    tmp_db.execute(
        "INSERT INTO ticket_paired_stage_openers(ticket_id,stage,opened_at) "
        "VALUES (?,'needs_success',1)",
        (ticket.id,),
    )
    captured = ConversationTurnReference("c_chief", 7)
    conversation = AsyncMock()
    monkeypatch.setattr(
        "planner.tickets.actions.message_delivery_service.revision_source_turn",
        AsyncMock(return_value=captured),
    )

    revised = asyncio.run(
        actions.return_ticket_for_revision(
            conversation,
            tmp_db,
            ticket.id,
            message="Revise.",
            ctx=RequestContext(CHIEF_PRINCIPAL),
            clock=fake_clock,
        )
    )

    assert revised.ticket_status.value == "empty"
    assert (
        tmp_db.execute(
            "SELECT 1 FROM ticket_paired_stage_openers WHERE ticket_id=? AND stage=?",
            (ticket.id, ticket.stage),
        ).fetchone()
        is None
    )
    conversation.record_explicit_reply.assert_awaited_once_with(
        captured, Principal(PrincipalKind.ticket, ticket.id)
    )


def test_revision_feedback_is_discarded_when_the_ticket_leaves_its_stage(
    tmp_db: Connection,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id='c_stage_scope' WHERE id=?", (ticket.id,)
    )
    data.return_for_revision(
        tmp_db,
        ticket.id,
        message="Revise only this stage.",
        principal=OWNER_PRINCIPAL,
        now=20,
    )
    data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="Revised success",
        recap="Revised",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=21,
    )
    data.accept_proposal(
        tmp_db,
        ticket.id,
        field="success",
        principal=OWNER_PRINCIPAL,
        now=22,
        next_ceiling="needs_approach",
        next_holder=OWNER_PRINCIPAL,
    )

    assert revision_feedback.snapshot(tmp_db, ticket.id) is None
    assert (
        tmp_db.execute(
            "SELECT 1 FROM ticket_revision_feedback WHERE ticket_id=?", (ticket.id,)
        ).fetchone()
        is None
    )


def test_new_revision_feedback_has_an_explicit_character_limit(tmp_db: Connection) -> None:
    accepted = _park(tmp_db, OWNER_PRINCIPAL)
    rejected = _park(tmp_db, OWNER_PRINCIPAL, now=30)
    tmp_db.executemany(
        "UPDATE tickets SET conversation_id=? WHERE id=?",
        (("c_limit_accepted", accepted.id), ("c_limit_rejected", rejected.id)),
    )

    data.return_for_revision(
        tmp_db,
        accepted.id,
        message="x" * REVISION_GUIDANCE_MAX_CHARACTERS,
        principal=OWNER_PRINCIPAL,
        now=20,
    )
    stored = revision_feedback.snapshot(tmp_db, accepted.id)
    assert stored is not None
    assert stored.items[0].message == "x" * REVISION_GUIDANCE_MAX_CHARACTERS

    with pytest.raises(PlannerError, match="at most 10000 characters"):
        data.return_for_revision(
            tmp_db,
            rejected.id,
            message="x" * (REVISION_GUIDANCE_MAX_CHARACTERS + 1),
            principal=OWNER_PRINCIPAL,
            now=40,
        )

    unchanged = data.read_ticket(tmp_db, rejected.id)
    assert unchanged.pending_proposal == rejected.pending_proposal
    assert revision_feedback.snapshot(tmp_db, rejected.id) is None


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


def test_a_sprint_item_holder_cannot_be_deleted_while_a_ticket_uses_it(
    tmp_db: Connection,
    fake_clock: Clock,
) -> None:
    item = sprints_data.create_item(
        tmp_db,
        title="Holder Item",
        project_id="project_vylo",
        clock=fake_clock,
    )
    data.create_ticket(
        tmp_db,
        title="Held",
        principal=Principal(PrincipalKind.sprint_item, item.id),
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note=None,
    )
    with pytest.raises(PlannerError, match="holds a Ticket ceiling"):
        sprints_data._delete_item_rows(tmp_db, item.id)  # noqa: SLF001


def test_migration_backfills_owner_and_startup_audits_holder_integrity(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "proposal-holder.db"
    engine = db_module._migration_engine(str(db_path), 5000)  # noqa: SLF001
    try:
        with engine.begin() as connection:
            command.upgrade(
                db_module._alembic_config(connection),  # noqa: SLF001
                "automatic_compaction_attempts",
            )
    finally:
        engine.dispose()
    before = connect(str(db_path))
    before.execute(
        "INSERT INTO tickets "
        "(id,title,worker_type,employee_backend,stage,ceiling,"
        "field_values,created_at,updated_at) "
        "VALUES ('t_old','Old','coding','codex','needs_kickoff','needs_kickoff','{}',1,1)"
    )
    before.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)
    assert data.read_ticket(upgraded, "t_old").ceiling_holder == OWNER_PRINCIPAL
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
            upgraded.execute(
                "UPDATE tickets SET ceiling_holder = ? WHERE id = 't_old'",
                (invalid_holder,),
            )
    upgraded.execute("PRAGMA ignore_check_constraints=ON")
    upgraded.execute(
        "UPDATE tickets SET ceiling_holder = ? WHERE id = 't_old'",
        ('{"id":"missing","kind":"ticket"}',),
    )
    upgraded.execute("PRAGMA ignore_check_constraints=OFF")
    with pytest.raises(RuntimeError, match="ceiling holder does not exist"):
        data.audit_ticket_registry_integrity(upgraded)
    upgraded.close()
