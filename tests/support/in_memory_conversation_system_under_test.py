"""Binds the in-memory conversation system to the conformance harness.

The binder is where one implementation's own vocabulary meets the suite's. It maps the
fake's observations onto ``RecordedFact`` and hands the suite the fake's backend
stand-in as the backend-side account. The real conversation system will get a binder of
the same shape over its durable events and its real child process.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from tests.support.conversation_contract_conformance import (
    BackendWrite,
    ConversationSystemUnderTest,
    RecordedFact,
    RecordedFactKind,
    RecordedTurnEnding,
)

from planner.conversation.contracts import ConversationSystem
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationObservation,
    InMemoryConversationSystem,
)


def _recorded_fact(observation: InMemoryConversationObservation) -> RecordedFact:
    return RecordedFact(
        kind=RecordedFactKind(observation.kind.value),
        text=observation.text,
        sender_label=observation.sender_label,
        mode=observation.mode,
        turn_ending=(
            None
            if observation.turn_ending is None
            else RecordedTurnEnding(observation.turn_ending.value)
        ),
        refusal_reason=observation.refusal_reason,
        permission_ask_id=observation.permission_ask_id,
        model=observation.model,
        reasoning_effort=observation.reasoning_effort,
    )


class InMemoryConversationSystemUnderTest:
    """The fake, dressed in the conformance harness's vocabulary."""

    def __init__(self, system: InMemoryConversationSystem) -> None:
        self._system = system

    @property
    def system(self) -> ConversationSystem:
        return self._system

    async def settle(self) -> None:
        # The fake never schedules work of its own, so yielding once to the event loop
        # is quiescence: anything it was going to do has already happened.
        await asyncio.sleep(0)

    async def backend_writes(self, conversation_id: str) -> tuple[BackendWrite, ...]:
        return tuple(
            BackendWrite(text=write.text, sender_label=write.sender_label, mode=write.mode)
            for write in self._system.backend_prompt_writes(conversation_id)
        )

    async def backend_permission_answer(self, conversation_id: str, ask_id: str) -> str | None:
        return self._system.backend_permission_answer(conversation_id, ask_id)

    async def backend_cancellations(self, conversation_id: str) -> int:
        return self._system.backend_cancellations(conversation_id)

    async def backend_model(self, conversation_id: str) -> str | None:
        return self._system.backend_model(conversation_id)

    async def backend_reasoning_effort(self, conversation_id: str) -> str | None:
        return self._system.backend_reasoning_effort(conversation_id)

    # The fake does all of its reacting inside the call, so these already satisfy the
    # harness's timing obligation: there is nothing left in flight when they return.

    async def complete_running_turn(self, conversation_id: str) -> None:
        self._system.complete_running_turn(conversation_id)

    async def fail_running_turn(self, conversation_id: str) -> None:
        self._system.fail_running_turn(conversation_id)

    async def raise_permission_ask(self, conversation_id: str) -> str:
        return self._system.raise_permission_ask(conversation_id)

    async def answer_permission_ask(
        self, conversation_id: str, ask_id: str, option_id: str
    ) -> bool:
        return self._system.answer_permission_ask(conversation_id, ask_id, option_id)

    async def arm_backend_start_failure(self, conversation_id: str) -> None:
        self._system.arm_backend_start_failure(conversation_id)

    async def arm_session_load_failure(self, conversation_id: str) -> None:
        self._system.arm_session_load_failure(conversation_id)

    async def arm_backend_write_failure(self, conversation_id: str) -> None:
        self._system.arm_backend_write_failure(conversation_id)

    async def recorded_facts(self, conversation_id: str) -> tuple[RecordedFact, ...]:
        return tuple(
            _recorded_fact(observation)
            for observation in self._system.observations(conversation_id)
        )


@asynccontextmanager
async def open_in_memory_conversation_system_under_test() -> AsyncIterator[
    ConversationSystemUnderTest
]:
    """Open a fake conversation system for one conformance test. Teardown is a no-op:
    there are no child processes, sessions or tasks to shut down."""
    yield InMemoryConversationSystemUnderTest(InMemoryConversationSystem())
