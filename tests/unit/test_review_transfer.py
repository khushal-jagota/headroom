"""The courtesy notice after a parked review changes hands.

Covers who it decides to tell (the new agent holder, the previous holder only on a
genuine third-party move) and, separately, that delivery reaches the right targets with
the right words.
"""

from __future__ import annotations

import asyncio
from sqlite3 import Connection
from unittest.mock import patch

import pytest

from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.conversation.message_content import text_message_content
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.managers import data as managers_data
from planner.runtime import conversation_start
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, ReviewTransferNotice
from planner.tickets.review_transfer import notify_review_transfers


def _parked_ticket(
    conn: Connection,
    clock: FakeClock,
    *,
    title: str,
    holder: Principal,
    manager_id: str | None = None,
) -> str:
    ticket = tickets_data.create_ticket(
        conn,
        title=title,
        principal=Principal(PrincipalKind.manager, manager_id)
        if manager_id is not None
        else holder,
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        stated_ceiling="needs_success_condition",
        manager_id=manager_id,
        stated_holder=holder,
    )
    tickets_data.file_current_proposal(
        conn,
        ticket.id,
        body="Proposed answer",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=clock.now_unix(),
    )
    return ticket.id


def _transfer(
    conn: Connection,
    clock: FakeClock,
    ticket_id: str,
    *,
    new_holder: Principal,
    actor: Principal,
) -> ReviewTransferNotice:
    notices: list[ReviewTransferNotice] = []
    tickets_data.edit_ticket(
        conn,
        ticket_id,
        edit={"ceiling_holder": new_holder},
        title_max_chars=TITLE_MAX_CHARS,
        principal=actor,
        now=clock.now_unix(),
        review_transfer_notices=notices,
    )
    assert len(notices) == 1
    return notices[0]


def test_previous_holder_moving_it_themselves_is_not_notified(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Self move by loser", holder=OWNER_PRINCIPAL
    )
    notice = _transfer(
        tmp_db, fake_clock, ticket_id, new_holder=CHIEF_PRINCIPAL, actor=OWNER_PRINCIPAL
    )
    assert notice.new_holder == CHIEF_PRINCIPAL
    assert notice.new_holder_name == "The Chief"
    assert notice.notify_previous_holder is False
    # The previous holder is not owed a message, but the new holder still is, and
    # that message needs to say who moved it.
    assert notice.mover_name == "The user"


def test_new_holder_moving_it_themselves_is_not_notified(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Self move by taker", holder=OWNER_PRINCIPAL
    )
    notice = _transfer(
        tmp_db, fake_clock, ticket_id, new_holder=CHIEF_PRINCIPAL, actor=CHIEF_PRINCIPAL
    )
    assert notice.notify_previous_holder is False
    assert notice.mover_name is None


def test_third_party_move_notifies_the_previous_holder_with_the_movers_name(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    mover_item = managers_data.create_manager(
        tmp_db, title="Reassigning supervisor", project_id="project_vylo", clock=fake_clock
    )
    ticket_id = _parked_ticket(
        tmp_db,
        fake_clock,
        title="Third party move",
        holder=OWNER_PRINCIPAL,
        manager_id=mover_item.id,
    )
    actor = CHIEF_PRINCIPAL
    new_holder = Principal(PrincipalKind.manager, mover_item.id)
    notice = _transfer(tmp_db, fake_clock, ticket_id, new_holder=new_holder, actor=actor)
    assert notice.previous_holder == OWNER_PRINCIPAL
    assert notice.new_holder == new_holder
    assert notice.new_holder_name == "Reassigning supervisor"
    assert notice.notify_previous_holder is True
    assert notice.mover_name == "The Chief"


def test_new_holder_can_be_a_manager(tmp_db: Connection, fake_clock: FakeClock) -> None:
    item = managers_data.create_manager(
        tmp_db, title="Owning sprint item", project_id="project_vylo", clock=fake_clock
    )
    ticket_id = _parked_ticket(
        tmp_db,
        fake_clock,
        title="Transferred to an item",
        holder=OWNER_PRINCIPAL,
        manager_id=item.id,
    )
    new_holder = Principal(PrincipalKind.manager, item.id)
    notice = _transfer(tmp_db, fake_clock, ticket_id, new_holder=new_holder, actor=new_holder)
    assert notice.new_holder == new_holder
    assert notice.new_holder_name == "Owning sprint item"
    assert notice.mover_name is None
    assert notice.notify_previous_holder is False

    system = InMemoryConversationSystem()
    asyncio.run(notify_review_transfers(system, tmp_db, [notice], now=fake_clock.now_unix()))

    conversation = conversation_start.read_agent_conversation(tmp_db, item.manager_agent_key)
    assert conversation is not None
    observations = system.observations(conversation)
    assert len(observations) == 1
    assert observations[0].kind is InMemoryConversationObservationKind.prompt_delivered
    assert observations[0].content == text_message_content(
        f"You now hold the review of {notice.ticket_title}."
    )
    assert tickets_data.read_ticket(tmp_db, ticket_id).conversation_id is None


def test_delivery_addresses_both_targets_on_a_third_party_move(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    mover_item = managers_data.create_manager(
        tmp_db, title="Reassigning supervisor", project_id="project_vylo", clock=fake_clock
    )
    ticket_id = _parked_ticket(
        tmp_db,
        fake_clock,
        title="Delivered both ways",
        holder=OWNER_PRINCIPAL,
        manager_id=mover_item.id,
    )
    new_holder = Principal(PrincipalKind.manager, mover_item.id)
    actor = CHIEF_PRINCIPAL
    notice = _transfer(tmp_db, fake_clock, ticket_id, new_holder=new_holder, actor=actor)
    assert notice.notify_previous_holder is True

    system = InMemoryConversationSystem()
    asyncio.run(notify_review_transfers(system, tmp_db, [notice], now=fake_clock.now_unix()))

    new_holder_conversation = conversation_start.read_agent_conversation(
        tmp_db, mover_item.manager_agent_key
    )
    assert new_holder_conversation is not None
    new_holder_observations = system.observations(new_holder_conversation)
    assert len(new_holder_observations) == 1
    assert new_holder_observations[0].kind is InMemoryConversationObservationKind.prompt_delivered
    assert new_holder_observations[0].content == text_message_content(
        f"The Chief moved the review of {notice.ticket_title} to you."
    )

    owner_conversation = tickets_data.read_ticket(tmp_db, ticket_id).conversation_id
    assert owner_conversation is not None
    owner_observations = system.observations(owner_conversation)
    message_to_owner = [
        observation
        for observation in owner_observations
        if observation.kind is InMemoryConversationObservationKind.message_to_owner
    ]
    assert len(message_to_owner) == 1
    assert message_to_owner[0].recipient == OWNER_PRINCIPAL
    assert message_to_owner[0].content == text_message_content(
        f"The Chief moved the review of {notice.ticket_title} to Reassigning supervisor."
    )


def test_owner_arrival_on_a_self_move_sends_no_notice(
    tmp_db: Connection, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Delivered one way", holder=CHIEF_PRINCIPAL
    )
    pending_proposal = tickets_data.read_ticket(tmp_db, ticket_id).pending_proposal
    notice = _transfer(
        tmp_db, fake_clock, ticket_id, new_holder=OWNER_PRINCIPAL, actor=OWNER_PRINCIPAL
    )
    assert notice.notify_previous_holder is False
    assert notice.mover_name is None

    system = InMemoryConversationSystem()
    with (
        patch.object(system, "start_conversation", wraps=system.start_conversation) as start,
        patch.object(
            system, "record_message_to_owner", wraps=system.record_message_to_owner
        ) as record,
    ):
        asyncio.run(notify_review_transfers(system, tmp_db, [notice], now=fake_clock.now_unix()))
    start.assert_not_called()
    record.assert_not_called()
    assert "review transfer notice failed" not in caplog.text

    ticket = tickets_data.read_ticket(tmp_db, ticket_id)
    assert ticket.conversation_id is None
    assert ticket.ceiling_holder == OWNER_PRINCIPAL
    assert ticket.pending_proposal == pending_proposal


def test_owner_arrival_with_a_named_mover_sends_no_notice(
    tmp_db: Connection, fake_clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Delivered with a name", holder=CHIEF_PRINCIPAL
    )
    pending_proposal = tickets_data.read_ticket(tmp_db, ticket_id).pending_proposal
    notice = _transfer(
        tmp_db, fake_clock, ticket_id, new_holder=OWNER_PRINCIPAL, actor=CHIEF_PRINCIPAL
    )
    assert notice.notify_previous_holder is False
    assert notice.mover_name == "The Chief"

    system = InMemoryConversationSystem()
    with (
        patch.object(system, "start_conversation", wraps=system.start_conversation) as start,
        patch.object(
            system, "record_message_to_owner", wraps=system.record_message_to_owner
        ) as record,
    ):
        asyncio.run(notify_review_transfers(system, tmp_db, [notice], now=fake_clock.now_unix()))
    start.assert_not_called()
    record.assert_not_called()
    assert "review transfer notice failed" not in caplog.text

    ticket = tickets_data.read_ticket(tmp_db, ticket_id)
    assert ticket.conversation_id is None
    assert ticket.ceiling_holder == OWNER_PRINCIPAL
    assert ticket.pending_proposal == pending_proposal
