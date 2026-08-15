"""Waking a Sprint Item's supervisor: what counts, and what stops a second wake.

Everything here runs against a real temporary SQLite file and the in-memory
conversation system. The per-Item send is a plain async function, so most of these
drive it directly with ``asyncio.run`` and no threads at all.

The one thing every test is really about: a wake is only ever sent because something
moved, never because something stands, and it says what moved in the past tense.
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
from planner.tickets.contracts import FieldSlot, Proposal, TicketStatus
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
        proposals: tuple[tuple[str, str, int], ...] = (),
    ) -> str:
        """A child Ticket of this Item, put directly into the state under test.

        It is created with no kickoff, so it carries no proposal of its own. Each entry
        in ``proposals`` is one parked proposal: its field, who proposed it, and when.
        """
        with self.connect() as conn:
            ticket = tickets_data.create_ticket(
                conn,
                worker_type="coding",
                title="A Ticket",
                actor="human",
                now=0,
                title_max_chars=200,
                kickoff_note=None,
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
            fields = ticket.fields
            for field, proposed_by, created_at in proposals:
                fields = fields_codec.with_slot(
                    fields,
                    field,
                    FieldSlot(
                        value=None,
                        proposal=Proposal(
                            body="a body", proposed_by=proposed_by, created_at=created_at
                        ),
                        user_note=None,
                    ),
                )
            if proposals:
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
            content=text_message_content("t_earlier entered a paired stage"),
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

    def wake_message(self, item_id: str) -> str | None:
        with self.connect() as conn:
            return sprint_item_supervisor_wake.sprint_item_wake_message(
                conn, item_id, conversation_id=self.supervisor_conversation(item_id)
            )

    def needs_supervisor(self, item_id: str) -> bool:
        return self.wake_message(item_id) is not None

    def prompts_sent(self, item_id: str) -> tuple[str, ...]:
        conversation_id = self.supervisor_conversation(item_id)
        if conversation_id is None:
            return ()
        return tuple(
            write.text
            for write in self.conversations.backend_prompt_writes(conversation_id)
        )


def _wake(ticket_id: str, line: str) -> str:
    """The whole message a wake about one Ticket carries."""
    return f"{ticket_id} {line}"


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


def test_a_wake_names_the_ticket_and_what_happened(world: _World) -> None:
    item_id = world.item()
    ticket_id = world.ticket(
        item_id,
        status=TicketStatus.awaiting_approval,
        proposals=(("implementation", "agent", 50),),
    )

    assert world.wake(item_id) is True

    assert world.prompts_sent(item_id) == (
        _wake(ticket_id, "proposed an implementation"),
    )


@pytest.mark.parametrize(
    ("status", "stage", "proposals", "expected"),
    [
        (
            TicketStatus.awaiting_approval,
            None,
            (("plan", "agent", 50),),
            "proposed a plan",
        ),
        (TicketStatus.paired, None, (), "entered a paired stage"),
        (TicketStatus.needs_user, None, (), "asked for human help"),
        (TicketStatus.errored, None, (), "hit a backend failure"),
        (TicketStatus.empty, "done", (), "finished"),
    ],
)
def test_every_trigger_says_what_happened(
    world: _World,
    status: TicketStatus,
    stage: str | None,
    proposals: tuple[tuple[str, str, int], ...],
    expected: str,
) -> None:
    """One line per trigger, and every one of them is a past event."""
    item_id = world.item()
    ticket_id = world.ticket(item_id, status=status, stage=stage, proposals=proposals)

    assert world.wake_message(item_id) == _wake(ticket_id, expected)


def test_a_line_names_the_most_recent_parked_proposal(world: _World) -> None:
    """Below its ceiling a Worker can park a proposal on a field already passed."""
    item_id = world.item()
    ticket_id = world.ticket(
        item_id,
        status=TicketStatus.awaiting_approval,
        proposals=(("approach", "agent", 40), ("plan", "agent", 90)),
    )

    assert world.wake_message(item_id) == _wake(ticket_id, "proposed a plan")


def test_a_proposal_that_cannot_be_read_still_wakes_the_supervisor(
    world: _World,
) -> None:
    """Waiting for approval with nothing readable parked. Say less, not nothing.

    Approval status is written when a proposal is filed, so this pair of facts does not
    occur in ordinary work. If it ever does, a vague line reaches the supervisor and
    silence does not.
    """
    item_id = world.item()
    ticket_id = world.ticket(item_id, status=TicketStatus.awaiting_approval)

    assert world.wake_message(item_id) == _wake(ticket_id, "proposed something")


def test_every_ticket_that_moved_gets_a_line_oldest_first(world: _World) -> None:
    item_id = world.item()
    first = world.ticket(item_id, status=TicketStatus.errored, status_changed_at=100)
    second = world.ticket(item_id, status=TicketStatus.needs_user, status_changed_at=200)

    assert world.wake_message(item_id) == (
        f"{_wake(first, 'hit a backend failure')}\n{second} asked for human help"
    )


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
        proposals=(("plan", SPRINT_ITEM_SUPERVISOR_ACTOR, 50),),
    )

    assert world.wake(item_id) is False
    assert world.prompts_sent(item_id) == ()


def test_a_worker_proposal_still_wakes_the_supervisor(world: _World) -> None:
    item_id = world.item()
    world.ticket(
        item_id,
        status=TicketStatus.awaiting_approval,
        proposals=(("plan", "agent", 50),),
    )

    assert world.wake(item_id) is True


def test_a_reply_to_a_parked_proposal_does_not_wake_the_supervisor(
    world: _World,
) -> None:
    """`paired` with a proposal still parked means the user replied to that proposal.

    Filing a proposal writes approval status first, and resolving one clears the
    proposal off the field. So this pair of facts has one cause, and it is the user's
    business rather than the supervisor's.
    """
    item_id = world.item()
    world.ticket(
        item_id,
        status=TicketStatus.paired,
        proposals=(("plan", "agent", 50),),
    )

    assert world.wake(item_id) is False
    assert world.prompts_sent(item_id) == ()


def test_a_quiet_ticket_does_not_take_the_others_line_away(world: _World) -> None:
    item_id = world.item()
    world.ticket(
        item_id,
        status=TicketStatus.paired,
        proposals=(("plan", "agent", 50),),
        status_changed_at=100,
    )
    moved = world.ticket(item_id, status=TicketStatus.errored, status_changed_at=200)

    assert world.wake_message(item_id) == _wake(moved, "hit a backend failure")


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
    ticket_id = world.ticket(
        item_id,
        status=TicketStatus.awaiting_approval,
        proposals=(("plan", "agent", 50),),
    )

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
    assert world.prompts_sent(item_id) == (_wake(ticket_id, "proposed a plan"),)


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
    ticket_id = world.ticket(item_id, status=TicketStatus.errored)

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
        assert world.prompts_sent(item_id) == (
            _wake(ticket_id, "hit a backend failure"),
        )
    finally:
        wake_loop.stop()
        asyncio_loop.call_soon_threadsafe(asyncio_loop.stop)
        thread.join(5)
        asyncio_loop.close()
