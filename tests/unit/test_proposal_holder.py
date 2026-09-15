"""The Ticket ceiling holder is the stable address and authority for its proposal."""

from __future__ import annotations

import asyncio
import inspect
import sqlite3
from dataclasses import replace
from pathlib import Path
from sqlite3 import Connection
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from alembic import command

from planner.conversation.contracts import (
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.core import db as db_module
from planner.core.authctx import RequestContext
from planner.core.clock import Clock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.days import data as days_data
from planner.message_delivery.contracts import MessageDeliveryResult
from planner.sprints import data as sprints_data
from planner.tickets import actions, data, views
from planner.tickets.contracts import TITLE_MAX_CHARS, AtCap, Ticket
from planner.tickets.logic import resolution
from planner.worker_types.configuration import configured_worker_type_registry


def _park(conn: Connection, holder: Principal, now: int = 10) -> Ticket:
    ticket = data.create_ticket(
        conn,
        title=f"Proposal for {holder.kind.value}",
        principal=holder,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Agreed kickoff",
        stated_ceiling="needs_success",
        stated_at_cap=AtCap.propose,
    )
    return data.file_current_proposal_with_recap(
        conn,
        ticket.id,
        body="Success proposal",
        recap="Proposal ready",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=now + 1,
    )


def test_creation_and_auto_accept_preserve_the_creating_principal(tmp_db: Connection) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Chief-owned scope",
        principal=CHIEF_PRINCIPAL,
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Kickoff",
        stated_ceiling="needs_approach",
        stated_at_cap=AtCap.propose,
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


@pytest.mark.parametrize("holder_kind", ["owner", "chief", "sprint_item", "ticket"])
def test_parked_proposal_wakes_its_exact_non_owner_holder(
    tmp_db: Connection,
    fake_clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    holder_kind: str,
) -> None:
    if holder_kind == "owner":
        holder = OWNER_PRINCIPAL
    elif holder_kind == "chief":
        holder = CHIEF_PRINCIPAL
    elif holder_kind == "sprint_item":
        item = sprints_data.create_item(
            tmp_db, title="Holder", project_id="project_vylo", clock=fake_clock
        )
        holder = Principal(PrincipalKind.sprint_item, item.id)
    else:
        holder_ticket = data.create_ticket(
            tmp_db,
            title="Holder Ticket",
            principal=OWNER_PRINCIPAL,
            now=2,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
            kickoff_note="Hold another Ticket",
        )
        holder = Principal(PrincipalKind.ticket, holder_ticket.id)

    ticket = data.create_ticket(
        tmp_db,
        title=f"Proposal held by {holder_kind}",
        principal=holder,
        now=3,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Agreed kickoff",
        stated_ceiling="needs_success",
        stated_at_cap=AtCap.propose,
    )
    sender = Principal(PrincipalKind.ticket, ticket.id)
    send = AsyncMock(
        return_value=MessageDeliveryResult(holder, "c_holder", PromptDeliveryStarted())
    )
    monkeypatch.setattr("planner.tickets.actions.message_delivery_service.send_message", send)
    conversations = object()

    parked = asyncio.run(
        actions.file_current_proposal(
            conversations,  # type: ignore[arg-type]
            tmp_db,
            ticket.id,
            body="Success proposal",
            recap="Ready",
            ctx=RequestContext(sender),
            clock=fake_clock,
        )
    )

    assert parked.pending_proposal is not None
    assert parked.ceiling_holder == holder
    if holder == OWNER_PRINCIPAL:
        send.assert_not_awaited()
    else:
        send.assert_awaited_once_with(
            conversations,
            tmp_db,
            fake_clock,
            RequestContext(sender),
            holder,
            actions.proposal_ready_message(ticket.id),
        )


def test_holder_wake_refusal_exposes_retryable_error_and_preserves_proposal(
    tmp_db: Connection,
    fake_clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Chief-held proposal",
        principal=CHIEF_PRINCIPAL,
        now=3,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Agreed kickoff",
        stated_ceiling="needs_success",
        stated_at_cap=AtCap.propose,
    )
    send = AsyncMock(
        return_value=MessageDeliveryResult(
            CHIEF_PRINCIPAL,
            "c_chief",
            PromptDeliveryRefused(PromptDeliveryRefusalReason.write_to_backend_failed),
        )
    )
    monkeypatch.setattr("planner.tickets.actions.message_delivery_service.send_message", send)

    with pytest.raises(PlannerError) as refused:
        asyncio.run(
            actions.file_current_proposal(
                object(),  # type: ignore[arg-type]
                tmp_db,
                ticket.id,
                body="Success proposal",
                recap="Ready",
                ctx=RequestContext(Principal(PrincipalKind.ticket, ticket.id)),
                clock=fake_clock,
            )
        )

    assert refused.value.code is ErrorCode.gateway_offline
    assert refused.value.detail["proposal_parked"] is True
    parked = data.read_ticket(tmp_db, ticket.id)
    assert parked.pending_proposal is not None
    assert parked.ceiling_holder == CHIEF_PRINCIPAL

    # Repeating the same proposal request is the retry door: it replaces the same one
    # pending draft and makes another wake attempt, never creating a second proposal.
    send.return_value = MessageDeliveryResult(
        CHIEF_PRINCIPAL, "c_chief", PromptDeliveryStarted()
    )
    retried = asyncio.run(
        actions.file_current_proposal(
            object(),  # type: ignore[arg-type]
            tmp_db,
            ticket.id,
            body="Success proposal",
            recap="Ready",
            ctx=RequestContext(Principal(PrincipalKind.ticket, ticket.id)),
            clock=fake_clock,
        )
    )
    assert retried.pending_proposal is not None
    assert retried.pending_proposal.body == "Success proposal"
    assert send.await_count == 2


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
            at_cap=AtCap.propose,
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
        at_cap=AtCap.propose,
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
            at_cap=AtCap.propose,
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
            AtCap.propose,
            OWNER_PRINCIPAL,
            worker_type_definition=configured_worker_type_registry().require("coding"),
        )


def test_canonical_approval_writer_requires_an_explicit_next_holder() -> None:
    parameter = inspect.signature(data.accept_proposal).parameters["next_holder"]
    assert parameter.default is inspect.Parameter.empty


def test_scope_cannot_retarget_a_pending_proposal(tmp_db: Connection) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    with pytest.raises(PlannerError, match="proposal is pending"):
        data.change_scope(
            tmp_db,
            ticket.id,
            ceiling="needs_plan",
            at_cap=AtCap.propose,
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


def test_supervisor_revision_checks_current_item_before_sending(
    tmp_db: Connection,
    fake_clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder_item = sprints_data.create_item(
        tmp_db,
        title="Holder Item",
        project_id="project_vylo",
        clock=fake_clock,
    )
    other_item = sprints_data.create_item(
        tmp_db,
        title="Other Item",
        project_id="project_vylo",
        clock=fake_clock,
    )
    holder = Principal(PrincipalKind.sprint_item, holder_item.id)
    ticket = data.create_ticket(
        tmp_db,
        title="Supervisor proposal",
        principal=holder,
        now=10,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Agreed kickoff",
        sprint_item_id=holder_item.id,
        stated_ceiling="needs_success",
        stated_at_cap=AtCap.propose,
    )
    ticket = data.file_current_proposal_with_recap(
        tmp_db,
        ticket.id,
        body="Success proposal",
        recap="Ready",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=11,
    )
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'c_worker' WHERE id = ?",
        (ticket.id,),
    )
    lifecycle = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(
        "planner.tickets.actions.message_delivery_service.send_ticket_system_message",
        lifecycle,
    )
    monkeypatch.setattr("planner.tickets.actions.message_delivery_service.send_message", send)

    with pytest.raises(PlannerError) as forbidden:
        asyncio.run(
            actions.return_ticket_for_revision(
                object(),  # type: ignore[arg-type]
                tmp_db,
                ticket.id,
                message="Revise.",
                ctx=RequestContext(holder),
                clock=fake_clock,
                supervisor_sprint_item_id=other_item.id,
            )
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden
    lifecycle.assert_not_awaited()
    send.assert_not_awaited()


def test_supervisor_revision_rechecks_reparent_between_lifecycle_and_comment(
    tmp_db: Connection,
    fake_clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    holder_item = sprints_data.create_item(
        tmp_db, title="Original", project_id="project_vylo", clock=fake_clock
    )
    other_item = sprints_data.create_item(
        tmp_db, title="New parent", project_id="project_vylo", clock=fake_clock
    )
    holder = Principal(PrincipalKind.sprint_item, holder_item.id)
    ticket = _park(tmp_db, holder)
    tmp_db.execute(
        "UPDATE tickets SET sprint_item_id = ?, conversation_id = 'c_worker' WHERE id = ?",
        (holder_item.id, ticket.id),
    )

    async def lifecycle_then_reparent(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        tmp_db.execute(
            "UPDATE tickets SET sprint_item_id = ? WHERE id = ?", (other_item.id, ticket.id)
        )
        return SimpleNamespace(fate=PromptDeliveryStarted())

    comment = AsyncMock()
    monkeypatch.setattr(
        "planner.tickets.actions.message_delivery_service.send_ticket_system_message",
        AsyncMock(side_effect=lifecycle_then_reparent),
    )
    monkeypatch.setattr(
        "planner.tickets.actions.message_delivery_service.send_message", comment
    )

    with pytest.raises(PlannerError) as raced:
        asyncio.run(
            actions.return_ticket_for_revision(
                object(),  # type: ignore[arg-type]
                tmp_db,
                ticket.id,
                message="Revise.",
                ctx=RequestContext(holder),
                clock=fake_clock,
                supervisor_sprint_item_id=holder_item.id,
            )
        )

    assert raced.value.code is ErrorCode.agent_forbidden
    comment.assert_not_awaited()
    unchanged = data.read_ticket(tmp_db, ticket.id)
    assert unchanged.pending_proposal == ticket.pending_proposal
    assert unchanged.ticket_status == ticket.ticket_status


def test_revision_comment_uses_send_message_and_refusal_preserves_the_proposal(
    tmp_db: Connection,
    fake_clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = _park(tmp_db, OWNER_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'c_worker' WHERE id = ?",
        (ticket.id,),
    )
    delivery_order: list[str] = []

    async def lifecycle_delivery(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        delivery_order.append("lifecycle")
        return SimpleNamespace(fate=PromptDeliveryStarted())

    async def refused_comment(*_args: Any, **_kwargs: Any) -> MessageDeliveryResult:
        delivery_order.append("comment")
        return MessageDeliveryResult(
            Principal(PrincipalKind.ticket, ticket.id),
            "c_worker",
            PromptDeliveryRefused(PromptDeliveryRefusalReason.write_to_backend_failed),
        )
    send = AsyncMock(side_effect=refused_comment)
    lifecycle = AsyncMock(side_effect=lifecycle_delivery)
    monkeypatch.setattr(
        "planner.tickets.actions.message_delivery_service.send_ticket_system_message",
        lifecycle,
    )
    monkeypatch.setattr("planner.tickets.actions.message_delivery_service.send_message", send)

    with pytest.raises(PlannerError) as refused:
        asyncio.run(
            actions.return_ticket_for_revision(
                object(),  # type: ignore[arg-type]
                tmp_db,
                ticket.id,
                message="Revise this boundary.",
                ctx=RequestContext(OWNER_PRINCIPAL),
                clock=fake_clock,
            )
        )
    assert refused.value.code is ErrorCode.gateway_offline
    assert delivery_order == ["lifecycle", "comment"]
    unchanged = data.read_ticket(tmp_db, ticket.id)
    assert unchanged.pending_proposal == ticket.pending_proposal
    assert unchanged.ticket_status == ticket.ticket_status
    call = send.await_args
    assert call is not None
    assert call.args[3] == RequestContext(OWNER_PRINCIPAL)
    assert call.args[4] == Principal(PrincipalKind.ticket, ticket.id)
    assert call.args[5] == "Revise this boundary."


def test_revision_rechecks_after_send_and_owner_override_becomes_holder(
    tmp_db: Connection,
    fake_clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket = _park(tmp_db, CHIEF_PRINCIPAL)
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'c_worker' WHERE id = ?",
        (ticket.id,),
    )

    async def delivered(*_args: Any, **_kwargs: Any) -> MessageDeliveryResult:
        current = data.read_ticket(tmp_db, ticket.id)
        assert current.pending_proposal == ticket.pending_proposal
        return MessageDeliveryResult(
            Principal(PrincipalKind.ticket, ticket.id),
            "c_worker",
            PromptDeliveryStarted(),
        )

    monkeypatch.setattr(
        "planner.tickets.actions.message_delivery_service.send_message",
        delivered,
    )
    monkeypatch.setattr(
        "planner.tickets.actions.message_delivery_service.send_ticket_system_message",
        AsyncMock(return_value=SimpleNamespace(fate=PromptDeliveryStarted())),
    )
    revised = asyncio.run(
        actions.return_ticket_for_revision(
            object(),  # type: ignore[arg-type]
            tmp_db,
            ticket.id,
            message="Revise.",
            ctx=RequestContext(OWNER_PRINCIPAL),
            clock=fake_clock,
        )
    )
    assert revised.pending_proposal is None
    assert revised.ceiling_holder == OWNER_PRINCIPAL


def test_migration_backfills_owner_and_startup_audits_holder_integrity(tmp_path: Path) -> None:
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
        "(id,title,worker_type,employee_backend,stage,ceiling,default_stage_ownership_mode,"
        "field_values,created_at,updated_at) "
        "VALUES ('t_old','Old','coding','codex','needs_kickoff','needs_kickoff','paired','{}',1,1)"
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
