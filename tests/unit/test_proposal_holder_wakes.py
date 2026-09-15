"""Durable, idempotent delivery of proposal-ready facts to non-owner holders."""

from __future__ import annotations

import asyncio
from sqlite3 import Connection
from unittest.mock import AsyncMock

import pytest

from planner.conversation.contracts import (
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import Clock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.message_delivery.contracts import MessageDeliveryResult
from planner.message_delivery.service import send_system_message
from planner.proposal_holder_wakes import data as wake_data
from planner.proposal_holder_wakes.contracts import proposal_ready_message
from planner.proposal_holder_wakes.runtime import deliver_pending_wakes
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, AtCap


def _target(conn: Connection, holder: Principal, *, now: int = 1) -> str:
    ticket = tickets_data.create_ticket(
        conn,
        title="Durable wake",
        principal=holder,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Work",
        stated_ceiling="needs_success",
        stated_at_cap=AtCap.propose,
    )
    return ticket.id


def _file(conn: Connection, ticket_id: str, *, body: str, now: int) -> None:
    tickets_data.file_current_proposal_with_recap(
        conn,
        ticket_id,
        body=body,
        recap=body,
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=now,
    )


def test_crash_before_send_recovers_once_and_duplicate_recovery_is_empty(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="First", now=2)
    send = AsyncMock(
        return_value=MessageDeliveryResult(
            CHIEF_PRINCIPAL, "c_chief", PromptDeliveryStarted()
        )
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )

    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 1  # type: ignore[arg-type]
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 0  # type: ignore[arg-type]
    send.assert_awaited_once()


def test_reconcile_restores_a_missing_intent_for_an_existing_pending_proposal(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Existing", now=2)
    tmp_db.execute("DELETE FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,))
    send = AsyncMock(
        return_value=MessageDeliveryResult(
            CHIEF_PRINCIPAL, "c_chief", PromptDeliveryStarted()
        )
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )

    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 1  # type: ignore[arg-type]
    call = send.await_args
    assert call is not None
    assert call.args[3] == CHIEF_PRINCIPAL
    assert call.kwargs["sender_message_id"] == f"proposal-holder-wake:{ticket_id}:1:1"


@pytest.mark.parametrize("holder_kind", ["chief", "sprint_item", "ticket"])
def test_panels_authored_wake_uses_the_exact_holder_conversation_path(
    tmp_db: Connection, fake_clock: Clock, holder_kind: str
) -> None:
    if holder_kind == "chief":
        holder = CHIEF_PRINCIPAL
    elif holder_kind == "sprint_item":
        item = sprints_data.create_item(
            tmp_db, title="Holder Item", project_id="project_vylo", clock=fake_clock
        )
        holder = Principal(PrincipalKind.sprint_item, item.id)
    else:
        holder = Principal(
            PrincipalKind.ticket,
            tickets_data.create_ticket(
                tmp_db,
                title="Holder Ticket",
                principal=OWNER_PRINCIPAL,
                now=1,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type="coding",
                kickoff_note="Hold",
            ).id,
        )
    system = InMemoryConversationSystem()
    message = "Canonical proposal is ready."
    result = asyncio.run(
        send_system_message(
            system,
            tmp_db,
            fake_clock,
            holder,
            message,
            sender_message_id=f"path:{holder_kind}",
        )
    )

    assert isinstance(result.fate, PromptDeliveryStarted)
    assert result.conversation_id is not None
    observation = system.observations(result.conversation_id)[-1]
    assert observation.text == message
    assert observation.sender_label == "Panels"
    assert observation.sender is None
    assert observation.recipient is None


def test_crash_after_success_replays_same_id_without_duplicate_transcript(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Crash window", now=2)
    wake = wake_data.due(tmp_db, now=fake_clock.now_unix(), ticket_id=ticket_id)[0]
    system = InMemoryConversationSystem()
    first = asyncio.run(
        send_system_message(
            system,
            tmp_db,
            fake_clock,
            wake.holder,
            wake.message,
            sender_message_id=wake.sender_message_id,
        )
    )
    assert first.conversation_id is not None
    system.complete_running_turn(first.conversation_id)

    # The prompt succeeded, but the process died before marking its outbox row.
    assert asyncio.run(deliver_pending_wakes(system, tmp_db, fake_clock)) == 1
    matching = [
        observed
        for observed in system.observations(first.conversation_id)
        if observed.text == proposal_ready_message(ticket_id)
    ]
    assert len(matching) == 1


def test_definite_refusal_advances_attempt_then_recovery_delivers_once(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Retry", now=2)
    attempted_ids: list[str] = []
    transcript: list[str] = []

    async def refuse_then_accept(
        _conversations: object,
        _conn: Connection,
        _clock: Clock,
        recipient: Principal,
        message: str,
        *,
        sender_message_id: str,
    ) -> MessageDeliveryResult:
        attempted_ids.append(sender_message_id)
        if len(attempted_ids) == 1:
            return MessageDeliveryResult(
                recipient,
                "c_chief",
                PromptDeliveryRefused(
                    PromptDeliveryRefusalReason.write_to_backend_failed
                ),
            )
        transcript.append(message)
        return MessageDeliveryResult(recipient, "c_chief", PromptDeliveryStarted())

    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        refuse_then_accept,
    )
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 0  # type: ignore[arg-type]
    tmp_db.execute(
        "UPDATE proposal_holder_wakes SET retry_at=? WHERE ticket_id=?",
        (fake_clock.now_unix(), ticket_id),
    )
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 1  # type: ignore[arg-type]

    assert attempted_ids == [
        f"proposal-holder-wake:{ticket_id}:1:1",
        f"proposal-holder-wake:{ticket_id}:1:2",
    ]
    assert transcript == [proposal_ready_message(ticket_id)]


def test_replacement_supersedes_generation_and_owner_never_gets_an_outbox_row(
    tmp_db: Connection,
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="First", now=2)
    _file(tmp_db, ticket_id, body="Replacement", now=3)
    row = tmp_db.execute(
        "SELECT proposal_generation,delivery_attempt,state FROM proposal_holder_wakes "
        "WHERE ticket_id=?",
        (ticket_id,),
    ).fetchone()
    assert row is not None
    assert tuple(row) == (2, 1, "pending")

    owner_ticket_id = _target(tmp_db, OWNER_PRINCIPAL, now=4)
    _file(tmp_db, owner_ticket_id, body="Owner review", now=5)
    assert (
        tmp_db.execute(
            "SELECT 1 FROM proposal_holder_wakes WHERE ticket_id=?", (owner_ticket_id,)
        ).fetchone()
        is None
    )


def test_approval_cancels_a_stale_pending_wake(tmp_db: Connection) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Approve me", now=2)
    tickets_data.accept_proposal(
        tmp_db,
        ticket_id,
        field="success",
        principal=CHIEF_PRINCIPAL,
        now=3,
        next_ceiling="needs_approach",
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    row = tmp_db.execute(
        "SELECT state FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,)
    ).fetchone()
    assert row is not None
    assert row["state"] == "cancelled"
