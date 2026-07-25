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

from planner.conversation2.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation2.in_memory_conversation_system import (
    InMemoryConversationObservationKind,
    InMemoryConversationSystem,
)
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime.conversation_start import (
    CONVERSATION_ID_PREFIX,
    agent_resolve,
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
        ).employee_session_id


def test_the_conversation_exists_before_the_ticket_points_at_it(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        watcher = _LinkWatchingConversationSystem(system, tmp_db, ticket.id)

        conversation_id = await start_ticket_conversation(
            watcher, tmp_db, ticket, _values(ticket.id), now=10
        )

        # The conversation already existed while the Ticket still pointed at nothing.
        assert watcher.link_when_the_conversation_existed is None
        assert read_ticket(tmp_db, ticket.id).employee_session_id == conversation_id
        # And the id the Ticket now holds names a conversation that really takes text.
        assert isinstance(
            await system.send(conversation_id, "hello", sender_label="loop"),
            PromptDeliveryStarted,
        )

    asyncio.run(exercise())


def test_starting_writes_the_link_and_the_last_chosen_configuration(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()

        conversation_id = await start_ticket_conversation(
            system, tmp_db, ticket, _values(ticket.id), now=10
        )

        assert conversation_id.startswith(CONVERSATION_ID_PREFIX)
        started = read_ticket(tmp_db, ticket.id)
        assert started.employee_session_id == conversation_id
        assert started.employee_backend == "claude"
        assert started.employee_launch_model == "opus"
        assert started.employee_launch_reasoning_effort == "high"
        assert started.updated_at == 10

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

        conversation_id = await start_ticket_conversation(
            _Recording(), tmp_db, ticket, _values(ticket.id), now=10
        )

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
        await start_ticket_conversation(system, tmp_db, ticket, _values(ticket.id), now=10)

        fate = await send_to_ticket_conversation(
            system,
            tmp_db,
            ticket.id,
            "work the step",
            sender_label="loop",
            model_change="sonnet",
            reasoning_effort_change="low",
            now=20,
        )

        assert isinstance(fate, PromptDeliveryStarted)
        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "sonnet"
        assert after.employee_launch_reasoning_effort == "low"
        assert after.employee_backend == "claude"
        assert after.employee_session_id is not None

    asyncio.run(exercise())


def test_a_change_to_the_model_alone_leaves_the_recorded_effort_where_it_was(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await start_ticket_conversation(system, tmp_db, ticket, _values(ticket.id), now=10)

        await send_to_ticket_conversation(
            system,
            tmp_db,
            ticket.id,
            "work the step",
            sender_label="loop",
            model_change="sonnet",
            now=20,
        )

        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "sonnet"
        assert after.employee_launch_reasoning_effort == "high"

    asyncio.run(exercise())


def test_a_send_that_carries_no_change_leaves_the_recorded_configuration_alone(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        await start_ticket_conversation(system, tmp_db, ticket, _values(ticket.id), now=10)

        await send_to_ticket_conversation(
            system, tmp_db, ticket.id, "work the step", sender_label="loop", now=20
        )

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
        conversation_id = await start_ticket_conversation(
            system, tmp_db, ticket, _values(ticket.id), now=10
        )
        # Put a turn on the agent, so the next run-when-free message is held.
        await system.send(conversation_id, "incumbent", sender_label="owner")

        fate = await send_to_ticket_conversation(
            system,
            tmp_db,
            ticket.id,
            "work the step",
            sender_label="loop",
            mode=PromptDeliveryMode.run_when_free,
            model_change="sonnet",
            now=20,
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
        conversation_id = await start_ticket_conversation(
            system, tmp_db, ticket, _values(ticket.id), now=10
        )
        system.arm_backend_write_failure(conversation_id)

        fate = await send_to_ticket_conversation(
            system,
            tmp_db,
            ticket.id,
            "work the step",
            sender_label="loop",
            model_change="sonnet",
            now=20,
        )

        assert isinstance(fate, PromptDeliveryRefused)
        assert fate.refusal_reason is PromptDeliveryRefusalReason.write_to_backend_failed
        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_launch_model == "opus"
        assert after.updated_at == 10

    asyncio.run(exercise())


def test_sending_into_a_ticket_that_has_no_conversation_is_an_error(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()

        with pytest.raises(PlannerError) as raised:
            await send_to_ticket_conversation(
                system, tmp_db, ticket.id, "work the step", sender_label="loop", now=20
            )

        assert raised.value.code is ErrorCode.not_found

    asyncio.run(exercise())


def test_resetting_stops_the_conversation_and_unlinks_it(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        conversation_id = await start_ticket_conversation(
            system, tmp_db, ticket, _values(ticket.id), now=10
        )
        await system.send(conversation_id, "running work", sender_label="loop")

        await reset_ticket_conversation(system, tmp_db, ticket.id, now=30)

        assert system.backend_cancellations(conversation_id) == 1
        assert await system.is_running(conversation_id) is False
        after = read_ticket(tmp_db, ticket.id)
        assert after.employee_session_id is None
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
        conversation_id = await start_ticket_conversation(
            system, tmp_db, ticket, _values(ticket.id), now=10
        )
        await system.send(conversation_id, "running work", sender_label="loop")
        held = await system.send(conversation_id, "held work", sender_label="owner")
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
        assert read_ticket(tmp_db, ticket.id).employee_session_id is None

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


def test_worker_resolve_lets_the_tickets_own_values_beat_the_worker_type_defaults(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        moved = ConversationStartValues(
            backend_key=ConversationBackendKey.hermes,
            model=None,
            reasoning_effort=None,
            role_materials=worker_conversation_role_materials(ticket.id),
            workspace_folder=_WORKSPACE,
            access=ConversationAccess.full,
        )
        await start_ticket_conversation(system, tmp_db, ticket, moved, now=10)

        values = worker_resolve(
            tmp_db, read_ticket(tmp_db, ticket.id), workspace_folder=_WORKSPACE
        )

        assert values.backend_key is ConversationBackendKey.hermes
        assert values.model is None
        assert values.reasoning_effort is None

    asyncio.run(exercise())


def test_worker_resolve_applies_an_override_over_what_it_read(
    tmp_db: Connection, ticket: Ticket
) -> None:
    values = worker_resolve(
        tmp_db,
        ticket,
        ConversationStartOverrides(backend_key=ConversationBackendKey.hermes),
        workspace_folder=_WORKSPACE,
    )

    assert values.backend_key is ConversationBackendKey.hermes
    assert values.model is None
    assert values.reasoning_effort is None


def test_agent_resolve_reads_the_managed_chief_launch_defaults(tmp_db: Connection) -> None:
    values = agent_resolve(tmp_db, workspace_folder=_WORKSPACE)

    assert values.backend_key is ConversationBackendKey.codex
    assert values.model == "gpt-5.6-sol"
    assert values.reasoning_effort == "medium"
    assert values.role_materials.identity_environment_variables == (("PLAN_ACTOR", "chief"),)
