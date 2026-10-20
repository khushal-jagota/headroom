"""The courtesy notice after a parked review changes hands.

Covers who it decides to tell (the new holder always, the previous holder only on a
genuine third-party move) and, separately, that delivery reaches the right targets with
the right words.
"""

from __future__ import annotations

import asyncio
from sqlite3 import Connection

from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.conversation.message_content import text_message_content
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, ReviewTransferNotice
from planner.tickets.review_transfer import notify_review_transfers


def _parked_ticket(
    conn: Connection,
    clock: FakeClock,
    *,
    title: str,
    holder: Principal,
    sprint_item_id: str | None = None,
) -> str:
    ticket = tickets_data.create_ticket(
        conn,
        title=title,
        principal=Principal(PrincipalKind.sprint_item, sprint_item_id)
        if sprint_item_id is not None
        else holder,
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        stated_ceiling="needs_success_condition",
        sprint_item_id=sprint_item_id,
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
    mover_item = sprints_data.create_item(
        tmp_db, title="Reassigning supervisor", project_id="project_vylo", clock=fake_clock
    )
    ticket_id = _parked_ticket(
        tmp_db,
        fake_clock,
        title="Third party move",
        holder=OWNER_PRINCIPAL,
        sprint_item_id=mover_item.id,
    )
    actor = Principal(PrincipalKind.sprint_item, mover_item.id)
    notice = _transfer(tmp_db, fake_clock, ticket_id, new_holder=CHIEF_PRINCIPAL, actor=actor)
    assert notice.previous_holder == OWNER_PRINCIPAL
    assert notice.new_holder == CHIEF_PRINCIPAL
    assert notice.new_holder_name == "The Chief"
    assert notice.notify_previous_holder is True
    assert notice.mover_name == "Reassigning supervisor"


def test_new_holder_can_be_a_ticket(tmp_db: Connection, fake_clock: FakeClock) -> None:
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Transferred to a ticket", holder=OWNER_PRINCIPAL
    )
    holder_ticket = tickets_data.create_ticket(
        tmp_db,
        title="Reviewer ticket",
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
    )
    new_holder = Principal(PrincipalKind.ticket, holder_ticket.id)
    notice = _transfer(tmp_db, fake_clock, ticket_id, new_holder=new_holder, actor=OWNER_PRINCIPAL)
    assert notice.new_holder == new_holder
    assert notice.new_holder_name == "Reviewer ticket"


def test_new_holder_can_be_a_sprint_item(tmp_db: Connection, fake_clock: FakeClock) -> None:
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Transferred to an item", holder=OWNER_PRINCIPAL
    )
    item = sprints_data.create_item(
        tmp_db, title="Owning sprint item", project_id="project_vylo", clock=fake_clock
    )
    new_holder = Principal(PrincipalKind.sprint_item, item.id)
    notice = _transfer(tmp_db, fake_clock, ticket_id, new_holder=new_holder, actor=OWNER_PRINCIPAL)
    assert notice.new_holder == new_holder
    assert notice.new_holder_name == "Owning sprint item"


def test_delivery_addresses_both_targets_on_a_third_party_move(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    mover_item = sprints_data.create_item(
        tmp_db, title="Reassigning supervisor", project_id="project_vylo", clock=fake_clock
    )
    ticket_id = _parked_ticket(
        tmp_db,
        fake_clock,
        title="Delivered both ways",
        holder=OWNER_PRINCIPAL,
        sprint_item_id=mover_item.id,
    )
    holder_ticket = tickets_data.create_ticket(
        tmp_db,
        title="Reviewer ticket",
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
    )
    new_holder = Principal(PrincipalKind.ticket, holder_ticket.id)
    actor = Principal(PrincipalKind.sprint_item, mover_item.id)
    notice = _transfer(tmp_db, fake_clock, ticket_id, new_holder=new_holder, actor=actor)
    assert notice.notify_previous_holder is True

    system = InMemoryConversationSystem()
    asyncio.run(notify_review_transfers(system, tmp_db, [notice], now=fake_clock.now_unix()))

    new_holder_conversation = tickets_data.read_ticket(tmp_db, holder_ticket.id).conversation_id
    assert new_holder_conversation is not None
    new_holder_observations = system.observations(new_holder_conversation)
    assert len(new_holder_observations) == 1
    assert new_holder_observations[0].kind is InMemoryConversationObservationKind.prompt_delivered
    assert new_holder_observations[0].content == text_message_content(
        f"Reassigning supervisor moved the review of {notice.ticket_title} to you."
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
        f"Reassigning supervisor moved the review of {notice.ticket_title} to Reviewer ticket."
    )


def test_delivery_addresses_only_the_new_holder_on_a_self_move(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Delivered one way", holder=CHIEF_PRINCIPAL
    )
    notice = _transfer(
        tmp_db, fake_clock, ticket_id, new_holder=OWNER_PRINCIPAL, actor=OWNER_PRINCIPAL
    )
    assert notice.notify_previous_holder is False

    system = InMemoryConversationSystem()
    asyncio.run(notify_review_transfers(system, tmp_db, [notice], now=fake_clock.now_unix()))

    owner_conversation = tickets_data.read_ticket(tmp_db, ticket_id).conversation_id
    assert owner_conversation is not None
    observations = system.observations(owner_conversation)
    message_to_owner = [
        observation
        for observation in observations
        if observation.kind is InMemoryConversationObservationKind.message_to_owner
    ]
    assert len(message_to_owner) == 1
    assert message_to_owner[0].content == text_message_content(
        f"You now hold the review of {notice.ticket_title}."
    )


def test_delivery_names_the_mover_in_the_new_holder_message(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    """The new holder is told who moved it to them, not just that it is theirs now.

    Here the previous holder is the mover, so the previous holder stays unnotified, but
    the new holder's own message still has to name them.
    """
    ticket_id = _parked_ticket(
        tmp_db, fake_clock, title="Delivered with a name", holder=CHIEF_PRINCIPAL
    )
    notice = _transfer(
        tmp_db, fake_clock, ticket_id, new_holder=OWNER_PRINCIPAL, actor=CHIEF_PRINCIPAL
    )
    assert notice.notify_previous_holder is False
    assert notice.mover_name == "The Chief"

    system = InMemoryConversationSystem()
    asyncio.run(notify_review_transfers(system, tmp_db, [notice], now=fake_clock.now_unix()))

    owner_conversation = tickets_data.read_ticket(tmp_db, ticket_id).conversation_id
    assert owner_conversation is not None
    message_to_owner = [
        observation
        for observation in system.observations(owner_conversation)
        if observation.kind is InMemoryConversationObservationKind.message_to_owner
    ]
    assert len(message_to_owner) == 1
    assert message_to_owner[0].content == text_message_content(
        f"The Chief moved the review of {notice.ticket_title} to you."
    )
