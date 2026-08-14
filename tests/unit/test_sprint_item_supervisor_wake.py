"""Waking a Sprint Item's supervisor: what counts, and what stops a second wake.

Everything here runs against a real temporary SQLite file and the in-memory
conversation system. The per-Item send is a plain async function, so most of these
drive it directly with ``asyncio.run`` and no threads at all.

The one thing every test is really about: a wake says nothing. It is only ever sent
because something moved, never because something stands.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from typing import cast

import pytest

from planner.conversation.contracts import ConversationSystem, PromptDeliveryMode
from planner.conversation.events import (
    PromptEventPayload,
    conversation_event_payload_kind,
    conversation_event_payload_to_canonical_json,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core.clock import TestClock
from planner.core.db import connect, create_schema
from planner.runtime import conversation_start, sprint_item_supervisor_wake
from planner.runtime.sprint_item_supervisor_wake_loop import (
    SprintItemSupervisorWakeLoop,
    send_supervisor_wake,
)
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import Proposal, TicketStatus
from planner.tickets.logic import fields_codec
from planner.tickets.logic.admission import SPRINT_ITEM_SUPERVISOR_ACTOR

FIXED_NOW = datetime(2026, 7, 6, 12, 0, 0).astimezone()
PROJECT_ID = "project_vylo"


class _World:
    """One temporary database, one conversation system, one clock."""

    def __init__(self, tmp_path: Path) -> None:
        self.db_path = str(tmp_path / "supervisor-wake.db")
        with connect(self.db_path) as conn:
            create_schema(conn)
        self.clock = TestClock(FIXED_NOW)
        self.conversations = InMemoryConversationSystem()

    def connect(self) -> sqlite3.Connection:
        return connect(self.db_path)

    def item(self, title: str = "An Item") -> str:
        with self.connect() as conn:
            return sprints_data.create_item(
                conn, title=title, project_id=PROJECT_ID, clock=self.clock
            ).id

    def ticket(
        self,
        item_id: str,
        *,
        status: TicketStatus = TicketStatus.empty,
        stage: str | None = None,
        status_changed_at: int = 100,
        proposed_by: str | None = None,
    ) -> str:
        """A child Ticket of this Item, put directly into the state under test."""
        with self.connect() as conn:
            ticket = tickets_data.create_ticket(
                conn,
                worker_type="coding",
                title="A Ticket",
                actor="human",
                now=0,
                title_max_chars=200,
            )
            conn.execute(
                "UPDATE tickets SET sprint_item_id = ?, ticket_status = ?, "
                "ticket_status_changed_at = ? WHERE id = ?",
                (item_id, status.value, status_changed_at, ticket.id),
            )
            if stage is not None:
                conn.execute(
                    "UPDATE tickets SET stage = ? WHERE id = ?", (stage, ticket.id)
                )
            if proposed_by is not None:
                fields = fields_codec.with_slot(
                    ticket.fields,
                    "kickoff",
                    fields_codec.get_slot(ticket.fields, "kickoff").__class__(
                        value=None,
                        proposal=_proposal(proposed_by),
                        user_note=None,
                    ),
                )
                conn.execute(
                    "UPDATE tickets SET fields = ? WHERE id = ?",
                    (fields_codec.fields_to_json(fields), ticket.id),
                )
            conn.commit()
        return ticket.id

    def supervisor_conversation(self, item_id: str) -> str | None:
        with self.connect() as conn:
            return conversation_start.read_agent_conversation(
                conn, sprints_data.supervisor_agent_key(item_id)
            )

    def wake(self, item_id: str) -> bool:
        return asyncio.run(
            send_supervisor_wake(
                item_id,
                connect_database=self.connect,
                conversation_system=cast(ConversationSystem, self.conversations),
            )
        )

    def record_wake(self, item_id: str, *, at: int) -> str:
        """Write the row a real wake leaves behind, through the real encoding.

        The in-memory conversation system keeps no record, so a test that is about what
        the record says has to put the record there. The payload is built and encoded by
        the conversation system's own code, so the shape this asserts against is the shape
        production writes.
        """
        conversation_id = self.supervisor_conversation(item_id) or "conv_test"
        payload = PromptEventPayload(
            content=text_message_content(sprint_item_supervisor_wake.WAKE_TEXT),
            sender_label=sprint_item_supervisor_wake.WAKE_SENDER_LABEL,
            mode=PromptDeliveryMode.run_when_free,
        )
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO conversations (conversation_id, backend_key, "
                "workspace_folder, access, latest_sequence, created_at) "
                "VALUES (?, 'claude', '/tmp', 'full', 0, 0)",
                (conversation_id,),
            )
            conn.execute(
                "UPDATE agents SET conversation_id = ? WHERE agent_key = ?",
                (conversation_id, sprints_data.supervisor_agent_key(item_id)),
            )
            conn.execute(
                "INSERT INTO conversation_events (conversation_id, sequence, kind, "
                "payload, created_at) VALUES (?, 1, ?, ?, ?)",
                (
                    conversation_id,
                    str(conversation_event_payload_kind(payload)),
                    conversation_event_payload_to_canonical_json(payload),
                    at,
                ),
            )
            conn.commit()
        return conversation_id

    def needs_supervisor(self, item_id: str) -> bool:
        with self.connect() as conn:
            return sprint_item_supervisor_wake.sprint_item_needs_supervisor(
                conn, item_id, conversation_id=self.supervisor_conversation(item_id)
            )

    def prompts_sent(self, item_id: str) -> tuple[str, ...]:
        conversation_id = self.supervisor_conversation(item_id)
        if conversation_id is None:
            return ()
        return tuple(
            write.text
            for write in self.conversations.backend_prompt_writes(conversation_id)
        )


def _proposal(actor: str) -> Proposal:
    return Proposal(body="a kickoff", proposed_by=actor, created_at=0)


def _waited_for(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    end = monotonic() + timeout
    while monotonic() < end:
        if predicate():
            return True
        sleep(0.01)
    return False


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


# --- what a wake says ----------------------------------------------------------


def test_a_wake_carries_no_ticket_id_and_no_fact(world: _World) -> None:
    item_id = world.item()
    ticket_id = world.ticket(item_id, status=TicketStatus.awaiting_approval)

    assert world.wake(item_id) is True

    sent = world.prompts_sent(item_id)
    assert sent == (sprint_item_supervisor_wake.WAKE_TEXT,)
    assert ticket_id not in sent[0]
    assert item_id not in sent[0]
    for word in ("review", "errored", "blocked", "done", "obligation"):
        assert word not in sent[0].lower()


# --- what makes an Item need its supervisor ------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        TicketStatus.awaiting_approval,
        TicketStatus.errored,
        TicketStatus.needs_user,
    ],
)
def test_a_ticket_wanting_attention_wakes_the_supervisor(
    world: _World, status: TicketStatus
) -> None:
    item_id = world.item()
    world.ticket(item_id, status=status)

    assert world.wake(item_id) is True


def test_a_finished_ticket_wakes_the_supervisor(world: _World) -> None:
    item_id = world.item()
    world.ticket(item_id, status=TicketStatus.empty, stage="done")

    assert world.wake(item_id) is True


def test_an_item_needing_nothing_sends_nothing(world: _World) -> None:
    item_id = world.item()
    world.ticket(item_id, status=TicketStatus.empty)

    assert world.wake(item_id) is False
    assert world.prompts_sent(item_id) == ()


def test_a_blocked_ticket_never_wakes_the_supervisor(world: _World) -> None:
    """Blocking is ordinary planned state. The supervisor built it on purpose."""
    item_id = world.item()
    world.ticket(item_id, status=TicketStatus.blocked)

    assert world.wake(item_id) is False
    assert world.prompts_sent(item_id) == ()


def test_the_supervisors_own_proposal_does_not_wake_it(world: _World) -> None:
    """A supervisor that parked its own proposal remembers doing it."""
    item_id = world.item()
    world.ticket(
        item_id,
        status=TicketStatus.awaiting_approval,
        proposed_by=SPRINT_ITEM_SUPERVISOR_ACTOR,
    )

    assert world.wake(item_id) is False
    assert world.prompts_sent(item_id) == ()


def test_a_worker_proposal_still_wakes_the_supervisor(world: _World) -> None:
    item_id = world.item()
    world.ticket(
        item_id, status=TicketStatus.awaiting_approval, proposed_by="agent"
    )

    assert world.wake(item_id) is True


# --- what stops a second wake --------------------------------------------------


def test_standing_state_alone_does_not_wake_the_supervisor_again(world: _World) -> None:
    """The Ticket sits there. Changes keep landing elsewhere. It is told once.

    This is the whole difference between waking on change and answering from state.
    Without it, a Ticket the supervisor cannot resolve would be raised again by every
    unrelated commit in Panels, for as long as it stood.
    """
    item_id = world.item()
    world.ticket(item_id, status=TicketStatus.awaiting_approval, status_changed_at=100)

    assert world.needs_supervisor(item_id) is True
    world.record_wake(item_id, at=200)

    for _ in range(5):
        assert world.needs_supervisor(item_id) is False
    assert world.wake(item_id) is False
    assert world.prompts_sent(item_id) == ()


def test_a_ticket_that_moves_after_the_wake_counts_again(world: _World) -> None:
    item_id = world.item()
    ticket_id = world.ticket(item_id, status=TicketStatus.awaiting_approval)
    world.record_wake(item_id, at=200)
    assert world.needs_supervisor(item_id) is False

    with world.connect() as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status = ?, ticket_status_changed_at = ? WHERE id = ?",
            (TicketStatus.errored.value, 300, ticket_id),
        )
        conn.commit()

    assert world.needs_supervisor(item_id) is True


def test_nothing_stacks_behind_a_wake_that_is_still_waiting(world: _World) -> None:
    item_id = world.item()
    ticket_id = world.ticket(item_id, status=TicketStatus.awaiting_approval)

    assert world.wake(item_id) is True
    conversation_id = world.supervisor_conversation(item_id)
    assert conversation_id is not None

    # The supervisor is still working, and a person has queued something behind it.
    asyncio.run(
        world.conversations.send(
            conversation_id, text_message_content("and another thing"), sender_label="user"
        )
    )
    assert asyncio.run(world.conversations.held_prompts(conversation_id))

    with world.connect() as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status_changed_at = ? WHERE id = ?",
            (10_000_000_000, ticket_id),
        )
        conn.commit()

    assert world.wake(item_id) is False
    assert world.prompts_sent(item_id) == (sprint_item_supervisor_wake.WAKE_TEXT,)


def test_a_busy_supervisor_is_not_woken(world: _World) -> None:
    item_id = world.item()
    ticket_id = world.ticket(item_id, status=TicketStatus.awaiting_approval)

    assert world.wake(item_id) is True
    conversation_id = world.supervisor_conversation(item_id)
    assert conversation_id is not None
    assert asyncio.run(world.conversations.is_running(conversation_id))

    with world.connect() as conn:
        conn.execute(
            "UPDATE tickets SET ticket_status_changed_at = ? WHERE id = ?",
            (10_000_000_000, ticket_id),
        )
        conn.commit()

    assert world.wake(item_id) is False


# --- one Item's answer is its own ----------------------------------------------


def test_another_items_ticket_does_not_wake_this_supervisor(world: _World) -> None:
    quiet_item = world.item("Quiet")
    noisy_item = world.item("Noisy")
    world.ticket(noisy_item, status=TicketStatus.errored)

    assert world.wake(quiet_item) is False
    assert world.wake(noisy_item) is True


# --- the loop only ever runs because something changed --------------------------


def test_the_loop_sends_nothing_until_the_change_signal_wakes_it(
    world: _World,
) -> None:
    """It has no timer. Standing state is not a reason, and time passing is not one."""
    item_id = world.item()
    world.ticket(item_id, status=TicketStatus.errored)

    asyncio_loop = asyncio.new_event_loop()
    thread = threading.Thread(target=asyncio_loop.run_forever, daemon=True)
    thread.start()
    wake_loop = SprintItemSupervisorWakeLoop(
        world.db_path,
        world.clock,
        conversation_system=cast(ConversationSystem, world.conversations),
        asyncio_loop=asyncio_loop,
    )
    try:
        wake_loop.start()
        sleep(0.2)
        assert world.prompts_sent(item_id) == ()

        wake_loop.wake()
        assert _waited_for(lambda: world.prompts_sent(item_id) != ())
        assert world.prompts_sent(item_id) == (sprint_item_supervisor_wake.WAKE_TEXT,)
    finally:
        wake_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()
