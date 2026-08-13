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

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationStartRequest,
    HeldPrompt,
    HeldPromptPromotionFate,
    HeldPromptPromotionMode,
    PromptDeliveryFate,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.core.contracts import Priority
from planner.core.errors import ErrorCode, PlannerError
from planner.projects.data import create_project, update_project
from planner.runtime import conversation_start as conversation_start_module
from planner.runtime.conversation_start import (
    CONVERSATION_ID_PREFIX,
    agent_resolve,
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
        actor="human",
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
        mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
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
        )

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
        return await self._system.promote_held_prompt(
            conversation_id, held_prompt_id, mode
        )

    async def discard_held_prompt(
        self, conversation_id: str, held_prompt_id: str
    ) -> bool:
        return await self._system.discard_held_prompt(
            conversation_id, held_prompt_id
        )

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


class _KillWatchingConversationSystem(InMemoryConversationSystem):
    def __init__(self, *, kill_fails: bool = False) -> None:
        super().__init__()
        self.killed: list[str] = []
        self.kill_fails = kill_fails

    async def kill(self, conversation_id: str) -> None:
        self.killed.append(conversation_id)
        if self.kill_fails:
            raise RuntimeError("cleanup failed")
        await super().kill(conversation_id)


async def _started(
    system: object, conn: Connection, ticket: Ticket, values: ConversationStartValues, *, now: int
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
    mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
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


def test_the_conversation_exists_before_the_ticket_points_at_it(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        watcher = _LinkWatchingConversationSystem(system, tmp_db, ticket.id)

        conversation_id = await _started(watcher, tmp_db, ticket, _values(ticket.id), now=10)

        # The conversation already existed while the Ticket still pointed at nothing.
        assert watcher.link_when_the_conversation_existed is None
        assert read_ticket(tmp_db, ticket.id).conversation_id == conversation_id
        # And the id the Ticket now holds names a conversation that really takes text.
        assert isinstance(
            await system.send(conversation_id, text_message_content("hello"), sender_label="loop"),
            PromptDeliveryStarted,
        )

    asyncio.run(exercise())


def test_starting_writes_the_link_and_the_last_chosen_configuration(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()

        conversation_id = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)

        assert conversation_id.startswith(CONVERSATION_ID_PREFIX)
        started = read_ticket(tmp_db, ticket.id)
        assert started.conversation_id == conversation_id
        assert started.employee_backend == "claude"
        assert started.employee_launch_model == "opus"
        assert started.employee_launch_reasoning_effort == "high"
        assert started.updated_at == 10
        assert _history(tmp_db, ticket.id) == [conversation_id]

    asyncio.run(exercise())


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


@pytest.mark.parametrize("kill_fails", [False, True])
def test_an_immutable_record_mismatch_refuses_association_and_kills_the_start(
    tmp_db: Connection, ticket: Ticket, kill_fails: bool
) -> None:
    async def exercise() -> None:
        conversation_id = "conv_mismatched"
        tmp_db.execute(
            "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
            "role_text, identity_environment_variables, access, created_at) "
            "VALUES (?, 'claude', 'opus', '/different-worktree', ?, ?, 'full', 1)",
            (
                conversation_id,
                worker_conversation_role_materials(ticket.id).role_text,
                '[["PLAN_ACTOR","worker"],["PLAN_TICKET_ID","' + ticket.id + '"]]'
            ),
        )
        system = _KillWatchingConversationSystem(kill_fails=kill_fails)

        with pytest.raises(RuntimeError, match="differ from its durable record") as raised:
            await start_ticket_conversation(
                system,
                tmp_db,
                ticket,
                _values(ticket.id),
                conversation_id=conversation_id,
                now=10,
            )

        assert system.killed == [conversation_id]
        assert read_ticket(tmp_db, ticket.id).conversation_id is None
        assert _history(tmp_db, ticket.id) == []
        if kill_fails:
            assert raised.value.__notes__ == [
                f"cleanup also failed for conversation {conversation_id}: cleanup failed"
            ]

    asyncio.run(exercise())


def test_the_start_request_carries_every_resolved_value(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        seen: list[ConversationStartRequest] = []

        class _Recording:
            async def start_conversation(self, request: ConversationStartRequest) -> None:
                seen.append(request)
                await system.start_conversation(request)

            async def send(
                self,
                conversation_id: str,
                content: MessageContent,
                *,
                sender_label: str,
                mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
                model_change: str | None = None,
                reasoning_effort_change: str | None = None,
            ) -> PromptDeliveryFate:
                return await system.send(
                    conversation_id,
                    content,
                    sender_label=sender_label,
                    mode=mode,
                    model_change=model_change,
                    reasoning_effort_change=reasoning_effort_change,
                )

            async def interrupt(self, conversation_id: str) -> None:
                await system.interrupt(conversation_id)

            async def kill(self, conversation_id: str) -> None:
                await system.kill(conversation_id)

            async def is_running(self, conversation_id: str) -> bool:
                return await system.is_running(conversation_id)

            async def has_pending_permission_ask(self, conversation_id: str) -> bool:
                return await system.has_pending_permission_ask(conversation_id)

            async def has_pending_user_input(self, conversation_id: str) -> bool:
                return await system.has_pending_user_input(conversation_id)

        conversation_id = await _started(_Recording(), tmp_db, ticket, _values(ticket.id), now=10)

        (request,) = seen
        assert request.conversation_id == conversation_id
        assert request.backend_key is ConversationBackendKey.claude
        assert request.model == "opus"
        assert request.reasoning_effort == "high"
        assert request.role_materials == worker_conversation_role_materials(ticket.id)
        assert request.workspace_folder == _WORKSPACE

    asyncio.run(exercise())


def test_a_send_carrying_a_change_records_it_once_the_delivery_started(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await _started(system, tmp_db, ticket, _values(ticket.id), now=10)

        fate = await _sent(
            system, tmp_db, ticket.id, "work the step", model="sonnet",
            reasoning_effort="low", now=20,
        )

        assert isinstance(fate, PromptDeliveryStarted)
        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "sonnet"
        assert after.employee_launch_reasoning_effort == "low"
        assert after.employee_backend == "claude"
        assert after.conversation_id is not None

    asyncio.run(exercise())


def test_a_change_to_the_model_alone_leaves_the_recorded_effort_where_it_was(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await _started(system, tmp_db, ticket, _values(ticket.id), now=10)

        await _sent(system, tmp_db, ticket.id, "work the step", model="sonnet", now=20)

        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "sonnet"
        assert after.employee_launch_reasoning_effort == "high"

    asyncio.run(exercise())


def test_a_send_that_carries_no_change_leaves_the_recorded_configuration_alone(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await _started(system, tmp_db, ticket, _values(ticket.id), now=10)

        await _sent(system, tmp_db, ticket.id, "work the step", now=20)

        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "opus"
        assert after.employee_launch_reasoning_effort == "high"
        assert after.updated_at == 10

    asyncio.run(exercise())


def test_a_change_on_a_held_message_records_nothing_yet(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        conversation_id = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        # Put a turn on the agent, so the next run-when-free message is held.
        await system.send(conversation_id, text_message_content("incumbent"), sender_label="owner")

        fate = await _sent(
            system, tmp_db, ticket.id, "work the step",
            mode=PromptDeliveryMode.run_when_free, model="sonnet", now=20,
        )

        assert isinstance(fate, PromptDeliveryQueued)
        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "opus"
        assert after.updated_at == 10

    asyncio.run(exercise())


def test_a_change_on_a_refused_delivery_records_nothing(
    tmp_db: Connection, ticket: Ticket
) -> None:
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


class _RelinkingConversationSystem:
    """The fake, which points the Ticket at a fresh conversation mid-operation.

    It stands in for what a New press does from the browser while the loop's send — or a
    reset's kill — is still out: both are awaited, and the Ticket can move underneath
    them.
    """

    def __init__(
        self,
        system: InMemoryConversationSystem,
        conn: Connection,
        ticket_id: str,
        relink_to: str,
    ) -> None:
        self._system = system
        self._conn = conn
        self._ticket_id = ticket_id
        self._relink_to = relink_to

    def _relink(self) -> None:
        self._conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (self._relink_to, self._ticket_id),
        )
        self._conn.commit()

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self._system.start_conversation(request)

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.run_when_free,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
    ) -> PromptDeliveryFate:
        fate = await self._system.send(
            conversation_id,
            content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
        )
        self._relink()
        return fate

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
        return await self._system.promote_held_prompt(
            conversation_id, held_prompt_id, mode
        )

    async def discard_held_prompt(
        self, conversation_id: str, held_prompt_id: str
    ) -> bool:
        return await self._system.discard_held_prompt(
            conversation_id, held_prompt_id
        )

    async def kill(self, conversation_id: str) -> None:
        await self._system.kill(conversation_id)
        self._relink()

    async def is_running(self, conversation_id: str) -> bool:
        return await self._system.is_running(conversation_id)

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        return await self._system.has_pending_permission_ask(conversation_id)

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        return await self._system.has_pending_user_input(conversation_id)


def test_a_change_is_not_recorded_on_a_ticket_that_moved_to_another_conversation(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        relinking = _RelinkingConversationSystem(system, tmp_db, ticket.id, "conv_elsewhere")

        fate = await _sent(relinking, tmp_db, ticket.id, "work the step", model="sonnet", now=20)

        # The delivery started, but by then the Ticket had been pointed at a different
        # conversation. Sonnet is true of the one that ran, not of the one it now names.
        assert isinstance(fate, PromptDeliveryStarted)
        after = read_ticket(tmp_db, ticket.id)
        assert after.conversation_id == "conv_elsewhere"
        assert after.employee_launch_model == "opus"
        assert after.employee_launch_reasoning_effort == "high"

    asyncio.run(exercise())


def test_resetting_does_not_unlink_a_conversation_it_did_not_kill(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        killed = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        await system.send(killed, text_message_content("running work"), sender_label="loop")
        relinking = _RelinkingConversationSystem(system, tmp_db, ticket.id, "conv_newer")

        await reset_ticket_conversation(relinking, tmp_db, ticket.id, now=30)

        # The old conversation was silenced, and the link the Ticket had moved on to is
        # left alone: only what this call killed is what it may cut loose.
        assert await system.is_running(killed) is False
        assert read_ticket(tmp_db, ticket.id).conversation_id == "conv_newer"

    asyncio.run(exercise())


def test_a_message_to_a_ticket_with_no_conversation_makes_one_and_goes_into_it(
    tmp_db: Connection, ticket: Ticket
) -> None:
    """A Ticket nobody has spoken to has no conversation, and the message is what makes one.

    On the values the message says it runs under, so the message that creates a
    conversation never has to change it — which is what a backend taking its model at
    startup would otherwise have to be restarted for.
    """

    async def exercise() -> None:
        system = InMemoryConversationSystem()
        created = new_conversation_id()

        delivered = await send_to_ticket_conversation(
            system,
            tmp_db,
            ticket.id,
            text_message_content("first words"),
            conversation_id=None,
            created_conversation_id=created,
            runs_under=ConversationStartOverrides(model="sonnet", reasoning_effort="low"),
            sender_label="owner",
            now=20,
        )

        assert isinstance(delivered.fate, PromptDeliveryStarted)
        assert delivered.conversation_id is not None
        after = read_ticket(tmp_db, ticket.id)
        assert after.conversation_id == delivered.conversation_id
        # Made on what the message said it runs under, rather than on stored defaults.
        assert after.employee_launch_model == "sonnet"
        assert after.employee_launch_reasoning_effort == "low"
        # And nothing had to be changed, because it was made on what was asked for.
        assert not [
            observation
            for observation in system.observations(delivered.conversation_id)
            if observation.kind is InMemoryConversationObservationKind.model_changed
        ]

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


def test_a_message_naming_a_conversation_the_ticket_is_not_in_is_refused(
    tmp_db: Connection, ticket: Ticket
) -> None:
    """A tab that missed a New must not go on talking to what New killed."""

    async def exercise() -> None:
        system = InMemoryConversationSystem()
        left_behind = await _started(system, tmp_db, ticket, _values(ticket.id), now=10)
        await reset_ticket_conversation(system, tmp_db, ticket.id, now=20)

        with pytest.raises(PlannerError) as raised:
            await send_to_ticket_conversation(
                system,
                tmp_db,
                ticket.id,
                text_message_content("still typing"),
                conversation_id=left_behind,
                sender_label="owner",
                now=30,
            )

        assert raised.value.code is ErrorCode.not_found

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


def test_resetting_discards_a_message_the_conversation_was_holding(
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
        held = await system.send(
            conversation_id,
            text_message_content("held work"),
            sender_label="owner",
        )
        assert isinstance(held, PromptDeliveryQueued)

        await reset_ticket_conversation(system, tmp_db, ticket.id, now=30)

        # Freeing the agent would have let the held message run. It never reached the
        # backend, and its discard is on the record rather than silent.
        assert [write.text for write in system.backend_prompt_writes(conversation_id)] == [
            "running work"
        ]
        discarded = [
            (observation.text, observation.sender_label)
            for observation in system.observations(conversation_id)
            if observation.kind is InMemoryConversationObservationKind.prompt_discarded
        ]
        assert discarded == [("held work", "owner")]
        assert await system.is_running(conversation_id) is False
        assert read_ticket(tmp_db, ticket.id).conversation_id is None

    asyncio.run(exercise())


def test_resetting_a_ticket_with_no_conversation_changes_nothing(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        before = read_ticket(tmp_db, ticket.id)

        await reset_ticket_conversation(system, tmp_db, ticket.id, now=30)

        assert read_ticket(tmp_db, ticket.id) == before

    asyncio.run(exercise())


def test_worker_resolve_reads_the_worker_types_managed_launch_defaults(
    tmp_db: Connection, ticket: Ticket
) -> None:
    values = worker_resolve(tmp_db, ticket, workspace_folder=_WORKSPACE)

    assert values.backend_key is ConversationBackendKey.codex
    assert values.model == "gpt-5.6-sol"
    assert values.reasoning_effort == "medium"
    assert values.role_materials == worker_conversation_role_materials(ticket.id)
    assert values.workspace_folder == _WORKSPACE


def test_worker_resolve_uses_an_existing_project_folder(
    tmp_db: Connection, ticket: Ticket, tmp_path: Path
) -> None:
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    project = create_project(
        tmp_db,
        name="Folder Project",
        priority=Priority.P1,
        folder_path=project_folder,
        now=2,
    )
    tmp_db.execute("UPDATE tickets SET project_id = ? WHERE id = ?", (project.id, ticket.id))

    values = worker_resolve(tmp_db, read_ticket(tmp_db, ticket.id))

    assert values.workspace_folder == project_folder.resolve()


@pytest.mark.parametrize("configured_path_kind", ["null", "missing", "file"])
def test_worker_resolve_falls_back_when_the_project_folder_is_not_an_existing_directory(
    tmp_db: Connection,
    ticket: Ticket,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_path_kind: str,
) -> None:
    fallback = tmp_path / "fallback"
    configured_path: Path | None = None
    if configured_path_kind == "missing":
        configured_path = tmp_path / "missing"
    elif configured_path_kind == "file":
        configured_path = tmp_path / "file"
        configured_path.write_text("not a directory", encoding="utf-8")
    project = create_project(
        tmp_db,
        name=f"Fallback {configured_path_kind}",
        priority=Priority.P1,
        folder_path=configured_path,
        now=2,
    )
    tmp_db.execute("UPDATE tickets SET project_id = ? WHERE id = ?", (project.id, ticket.id))
    monkeypatch.setattr(conversation_start_module, "_default_workspace_folder", lambda: fallback)

    values = worker_resolve(tmp_db, read_ticket(tmp_db, ticket.id))

    assert values.workspace_folder == fallback


def test_worker_resolve_falls_back_when_the_ticket_has_no_project(
    tmp_db: Connection,
    ticket: Ticket,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback = tmp_path / "fallback"
    monkeypatch.setattr(conversation_start_module, "_default_workspace_folder", lambda: fallback)

    assert worker_resolve(tmp_db, ticket).workspace_folder == fallback


def test_worker_resolve_explicit_folder_beats_the_project_folder(
    tmp_db: Connection, ticket: Ticket, tmp_path: Path
) -> None:
    project_folder = tmp_path / "project"
    project_folder.mkdir()
    project = create_project(
        tmp_db,
        name="Override Project",
        priority=Priority.P1,
        folder_path=project_folder,
        now=2,
    )
    tmp_db.execute("UPDATE tickets SET project_id = ? WHERE id = ?", (project.id, ticket.id))

    assert (
        worker_resolve(
            tmp_db, read_ticket(tmp_db, ticket.id), workspace_folder=_WORKSPACE
        ).workspace_folder
        == _WORKSPACE
    )


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
        tmp_db.execute(
            "UPDATE tickets SET project_id = ? WHERE id = ?", (project.id, ticket.id)
        )
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


def test_worker_resolve_lets_the_tickets_own_values_beat_the_worker_type_defaults(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        moved = ConversationStartValues(
            backend_key=ConversationBackendKey.hermes,
            model="openai-codex:gpt-5.6-sol",
            reasoning_effort=None,
            role_materials=worker_conversation_role_materials(ticket.id),
            workspace_folder=_WORKSPACE,
            access=ConversationAccess.full,
        )
        await _started(system, tmp_db, ticket, moved, now=10)

        values = worker_resolve(
            tmp_db, read_ticket(tmp_db, ticket.id), workspace_folder=_WORKSPACE
        )

        assert values.backend_key is ConversationBackendKey.hermes
        assert values.model == "openai-codex:gpt-5.6-sol"
        assert values.reasoning_effort is None

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


def test_worker_resolve_applies_an_override_over_what_it_read(
    tmp_db: Connection, ticket: Ticket
) -> None:
    values = worker_resolve(
        tmp_db,
        ticket,
        ConversationStartOverrides(
            backend_key=ConversationBackendKey.hermes,
            model="openai-codex:gpt-5.6-sol",
        ),
        workspace_folder=_WORKSPACE,
    )

    assert values.backend_key is ConversationBackendKey.hermes
    assert values.model == "openai-codex:gpt-5.6-sol"
    assert values.reasoning_effort is None


def test_agent_resolve_reads_the_managed_chief_launch_defaults(tmp_db: Connection) -> None:
    values = agent_resolve(tmp_db, workspace_folder=_WORKSPACE)

    assert values.backend_key is ConversationBackendKey.codex
    assert values.model == "gpt-5.6-sol"
    assert values.reasoning_effort == "medium"
    assert values.role_materials.identity_environment_variables == (("PLAN_ACTOR", "chief"),)
