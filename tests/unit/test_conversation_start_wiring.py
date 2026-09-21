"""Starting, sending into, and resetting a Ticket's conversation, against the fake.

The conversation system here is the in-memory reference implementation of the contract,
and the database is a real SQLite file, so every claim about what was written is read
back out of the Ticket row.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import (
    AddressedPromptDeliveryReceipt,
    ConversationAccess,
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationTurnReference,
    HeldPrompt,
    HeldPromptPromotionFate,
    HeldPromptPromotionMode,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.core.contracts import Principal, Priority
from planner.projects.data import create_project, update_project
from planner.runtime.conversation_start import (
    new_conversation_id,
    reset_ticket_conversation,
    send_to_ticket_conversation,
    start_ticket_conversation,
    worker_resolve,
)
from planner.runtime.logic.conversation_start_resolution import (
    ConversationStartOverrides,
    ConversationStartValues,
    worker_conversation_role_materials,
)
from planner.tickets.contracts import Ticket
from planner.tickets.data import create_ticket, read_ticket

_WORKSPACE = Path("/tmp/panels-workspace")


@pytest.fixture
def ticket(tmp_db: Connection) -> Ticket:
    return create_ticket(
        tmp_db,
        title="A conversation ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )


def _values(ticket_id: str) -> ConversationStartValues:
    return ConversationStartValues(
        backend_key=ConversationBackendKey.claude,
        model="opus",
        reasoning_effort="high",
        role_materials=worker_conversation_role_materials(ticket_id),
        workspace_folder=_WORKSPACE,
        access=ConversationAccess.full,
    )


def _history(conn: Connection, ticket_id: str) -> list[str]:
    return [
        str(row["conversation_id"])
        for row in conn.execute(
            "SELECT conversation_id FROM ticket_conversations "
            "WHERE ticket_id = ? ORDER BY conversation_id",
            (ticket_id,),
        )
    ]


class _LinkWatchingConversationSystem:
    """The fake, plus a note of what the Ticket's link said the moment it existed."""

    def __init__(
        self, system: InMemoryConversationSystem, conn: Connection, ticket_id: str
    ) -> None:
        self._system = system
        self._conn = conn
        self._ticket_id = ticket_id
        self.link_when_the_conversation_existed: str | None = "not observed"

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self._system.start_conversation(request)
        self.link_when_the_conversation_existed = read_ticket(
            self._conn, self._ticket_id
        ).conversation_id

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
    ) -> PromptDeliveryFate:
        return await self._system.send(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
        )

    async def send_with_receipt(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
        sender: Principal | None = None,
        recipient: Principal | None = None,
        reply_requested: bool = True,
    ) -> AddressedPromptDeliveryReceipt:
        return await self._system.send_with_receipt(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
            sender=sender,
            recipient=recipient,
            reply_requested=reply_requested,
        )

    async def record_message_to_owner(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        sender: Principal,
        recipient: Principal,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
    ) -> None:
        await self._system.record_message_to_owner(
            conversation_id,
            content,
            sender_label=sender_label,
            sender=sender,
            recipient=recipient,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
        )

    async def active_turn_reference(self, conversation_id: str) -> ConversationTurnReference | None:
        return await self._system.active_turn_reference(conversation_id)

    async def turn_expects_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> bool:
        return await self._system.turn_expects_reply(turn, recipient)

    async def record_explicit_reply(
        self, turn: ConversationTurnReference, recipient: Principal
    ) -> None:
        await self._system.record_explicit_reply(turn, recipient)

    async def interrupt(self, conversation_id: str) -> None:
        await self._system.interrupt(conversation_id)

    async def held_prompts(self, conversation_id: str) -> tuple[HeldPrompt, ...]:
        return await self._system.held_prompts(conversation_id)

    async def promote_held_prompt(
        self,
        conversation_id: str,
        held_prompt_id: str,
        mode: HeldPromptPromotionMode,
    ) -> HeldPromptPromotionFate | None:
        return await self._system.promote_held_prompt(conversation_id, held_prompt_id, mode)

    async def discard_held_prompt(self, conversation_id: str, held_prompt_id: str) -> bool:
        return await self._system.discard_held_prompt(conversation_id, held_prompt_id)

    async def kill(self, conversation_id: str) -> None:
        await self._system.kill(conversation_id)

    async def is_running(self, conversation_id: str) -> bool:
        return await self._system.is_running(conversation_id)

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return await self._system.has_pending_permission_ask(conversation_id)

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return await self._system.has_pending_user_input(conversation_id)


class _WhoseFirstWriteFails(_LinkWatchingConversationSystem):
    """The fake, with every conversation it makes unable to take a write.

    Armed as the conversation comes into being, because that is the only moment between a
    first message making one and that message being written to it.
    """

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self._system.start_conversation(request)
        self._system.arm_backend_write_failure(request.conversation_id)


async def _started(
    system: object,
    conn: Connection,
    ticket: Ticket,
    values: ConversationStartValues,
    *,
    now: int,
) -> str:
    """The conversation a Ticket is in after one is started for it."""
    conversation_id = new_conversation_id()
    linked = await start_ticket_conversation(
        system,  # type: ignore[arg-type]
        conn,
        ticket,
        values,
        conversation_id=conversation_id,
        now=now,
    )
    return linked.conversation_id


async def _sent(
    system: object,
    conn: Connection,
    ticket_id: str,
    text: str,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    mode: PromptDeliveryMode = PromptDeliveryMode.queue,
    sender_label: str = "loop",
    now: int,
) -> PromptDeliveryFate:
    """A message into the conversation the Ticket is in, and what became of it."""
    delivered = await send_to_ticket_conversation(
        system,  # type: ignore[arg-type]
        conn,
        ticket_id,
        text_message_content(text),
        conversation_id=read_ticket(conn, ticket_id).conversation_id,
        runs_under=ConversationStartOverrides(model=model, reasoning_effort=reasoning_effort),
        sender_label=sender_label,
        mode=mode,
        now=now,
    )
    return delivered.fate


def test_a_start_that_loses_the_active_pointer_race_records_no_history(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        winner = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        loser = new_conversation_id()

        linked = await start_ticket_conversation(
            system,
            tmp_db,
            ticket,
            _values(ticket.id),
            conversation_id=loser,
            now=20,
        )

        assert linked.conversation_id == winner
        assert linked.made_here is False
        assert _history(tmp_db, ticket.id) == [winner]

    asyncio.run(exercise())


def test_a_change_on_a_refused_delivery_records_nothing(tmp_db: Connection, ticket: Ticket) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        conversation_id = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        system.arm_backend_write_failure(conversation_id)

        fate = await _sent(system, tmp_db, ticket.id, "work the step", model="sonnet", now=20)

        assert isinstance(fate, PromptDeliveryRefused)
        assert fate.refusal_reason is PromptDeliveryRefusalReason.write_to_backend_failed
        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "opus"
        assert after.updated_at == 10

    asyncio.run(exercise())


def test_a_first_message_that_is_refused_leaves_the_ticket_with_no_conversation(
    tmp_db: Connection, ticket: Ticket
) -> None:
    """Making a conversation and saying the first thing in it are one act, or neither.

    A conversation the message never reached is one nobody can see and nobody can use, and
    leaving it linked is what put a Ticket somewhere it could not send from at all.
    """

    async def exercise() -> None:
        system = InMemoryConversationSystem()
        refusing = _WhoseFirstWriteFails(system, tmp_db, ticket.id)
        created = new_conversation_id()

        delivered = await send_to_ticket_conversation(
            refusing,
            tmp_db,
            ticket.id,
            text_message_content("first words"),
            conversation_id=None,
            created_conversation_id=created,
            sender_label="owner",
            now=20,
        )

        assert isinstance(delivered.fate, PromptDeliveryRefused)
        assert delivered.conversation_id is None
        assert read_ticket(tmp_db, ticket.id).conversation_id is None
        assert _history(tmp_db, ticket.id) == []

    asyncio.run(exercise())


def test_resetting_stops_the_conversation_and_unlinks_it(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        conversation_id = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        await system.send(
            conversation_id,
            text_message_content("running work"),
            sender_label="loop",
        )

        await reset_ticket_conversation(system, tmp_db, ticket.id, now=30)

        assert system.backend_cancellations(conversation_id) == 1
        assert await system.is_running(conversation_id) is False
        after = read_ticket(tmp_db, ticket.id)
        assert after.conversation_id is None
        assert _history(tmp_db, ticket.id) == [conversation_id]
        # The last-chosen values stay: they are what the next conversation starts from.
        assert after.employee_backend == "claude"
        assert after.employee_launch_model == "opus"
        assert after.employee_launch_reasoning_effort == "high"
        assert after.updated_at == 30

    asyncio.run(exercise())


def test_an_existing_conversation_keeps_its_workspace_after_the_project_folder_changes(
    tmp_db: Connection, ticket: Ticket, tmp_path: Path
) -> None:
    async def exercise() -> None:
        original_folder = tmp_path / "original"
        replacement_folder = tmp_path / "replacement"
        original_folder.mkdir()
        replacement_folder.mkdir()
        project = create_project(
            tmp_db,
            name="Moved Project",
            priority=Priority.P1,
            folder_path=original_folder,
            now=2,
        )
        tmp_db.execute("UPDATE tickets SET project_id = ? WHERE id = ?", (project.id, ticket.id))
        system = InMemoryConversationSystem()
        conversation_id = await _started(
            system,
            tmp_db,
            read_ticket(tmp_db, ticket.id),
            worker_resolve(tmp_db, read_ticket(tmp_db, ticket.id)),
            now=3,
        )

        update_project(tmp_db, project.id, folder_path=replacement_folder, now=4)
        fate = await _sent(system, tmp_db, ticket.id, "continue", now=5)

        assert isinstance(fate, PromptDeliveryStarted)
        stored_workspace = tmp_db.execute(
            "SELECT workspace_folder FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        assert stored_workspace is not None
        assert Path(str(stored_workspace["workspace_folder"])) == original_folder.resolve()

    asyncio.run(exercise())


def test_worker_resolve_falls_to_the_worker_type_defaults_when_the_ticket_names_no_model(
    tmp_db: Connection, ticket: Ticket
) -> None:
    # A row left from when a Ticket could name a backend and no model. It has not chosen
    # anything runnable — its backend and another backend's model are not a pair — so the
    # Worker type's own defaults answer whole, backend included.
    with tmp_db:
        tmp_db.execute(
            "UPDATE tickets SET employee_backend = 'hermes', employee_launch_model = NULL, "
            "employee_launch_reasoning_effort = NULL WHERE id = ?",
            (ticket.id,),
        )

    values = worker_resolve(tmp_db, read_ticket(tmp_db, ticket.id), workspace_folder=_WORKSPACE)

    assert values.backend_key is ConversationBackendKey.codex
    assert values.model == "gpt-5.6-sol"
    assert values.reasoning_effort == "medium"


