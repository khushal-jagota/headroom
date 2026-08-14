"""The conformance contract every conversation system must satisfy.

This suite is implementation-agnostic. It talks to a subject only through
``ConversationSystemUnderTest``: the contract object itself, a way to drive the backend
side, and two ways to look — at what actually reached the backend, and at what the
implementation recorded.

The two are kept apart on purpose. A fate like "started" claims the text reached a live
backend, and an implementation's own record of what it did cannot prove that claim
without arguing in a circle. ``backend_writes`` is the backend side's own account.

Collect the suite by subclassing it from a ``tests/unit`` module and returning a factory
for your subject. The base class is deliberately not named ``Test*`` so pytest collects
it exactly once, through the subclass.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Protocol

import pytest

from planner.conversation.contracts import (
    ConversationAlreadyStarted,
    ConversationBackendKey,
    ConversationStartRequest,
    ConversationSystem,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
)
from planner.conversation.message_content import (
    MessageContent,
    MessageText,
    message_content_text,
    text_message_content,
)


@dataclass(frozen=True, slots=True)
class BackendWrite:
    """One prompt write the backend side actually received, in the order received.

    ``content`` is the message the backend was handed, whole. ``text`` is the words in it,
    which is what nearly every exercise here asks about — a message with a picture in it
    is asserted against ``content``, and one that is only words reads the same as it
    always did.
    """

    content: MessageContent
    sender_label: str
    mode: PromptDeliveryMode

    @property
    def text(self) -> str:
        return message_content_text(self.content)


class RecordedFactKind(StrEnum):
    """What an implementation recorded. The suite's own vocabulary.

    Event record shapes are deferred to the real build, so the suite needs a vocabulary
    of its own: it must be able to assert *that* a fact was recorded without fixing *how*
    an implementation records it. Each subject's binder maps its own recording onto this.
    """

    prompt_delivered = "prompt_delivered"
    prompt_delivery_refused = "prompt_delivery_refused"
    prompt_discarded = "prompt_discarded"
    turn_ended = "turn_ended"
    permission_asked = "permission_asked"
    permission_answered = "permission_answered"
    model_changed = "model_changed"


class RecordedTurnEnding(StrEnum):
    """How a recorded turn ending ended."""

    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


@dataclass(frozen=True, slots=True)
class RecordedFact:
    """One recorded fact, normalised into the suite's vocabulary.

    ``content`` is the message the fact is about, when it is about one. ``text`` is its
    words, which is what the exercises that predate a message being more than words ask
    for.
    """

    kind: RecordedFactKind
    content: MessageContent | None = None
    sender_label: str | None = None
    mode: PromptDeliveryMode | None = None
    turn_ending: RecordedTurnEnding | None = None
    refusal_reason: PromptDeliveryRefusalReason | None = None
    permission_ask_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None

    @property
    def text(self) -> str | None:
        return None if self.content is None else message_content_text(self.content)


class ConversationSystemUnderTest(Protocol):
    """A conversation system plus everything a conformance test must be able to do to it.

    **The driving methods carry a timing obligation.** Each of them makes something
    happen on the backend side, and each returns only once the implementation under test
    has fully processed that backend event — including anything the event sets off, such
    as a held message being dequeued and written to the backend. A binder that returns as
    soon as the backend was poked, leaving the system still catching up, turns every
    consequence the suite asserts into a race. Only a binder can honour this, so it is
    stated here rather than papered over with sleeps in the tests.
    """

    @property
    def system(self) -> ConversationSystem: ...

    async def settle(self) -> None:
        """Drive the implementation to quiescence: everything it was going to do on its
        own has now happened. The fake yields to the event loop; a real system awaits its
        in-flight tasks."""

    # Backend-side observation, sourced independently of the system's own recording.
    async def backend_writes(self, conversation_id: str) -> tuple[BackendWrite, ...]:
        """Every prompt write the backend side has received so far, in order.

        It drains whatever the backend side can already read and returns; it never waits
        for more to arrive. The suite depends on that in both directions: a started or
        injected fate must show its write the instant ``send`` returns, and a queued
        message must still be absent after ``settle``. A binder that blocked until the
        agent had consumed its input would hang the queued case, and one that waited for
        nothing at all would race the started case — so a process-backed subject needs a
        scripted agent that reads promptly.
        """

    async def backend_permission_answer(self, conversation_id: str, ask_id: str) -> str | None: ...

    async def backend_cancellations(self, conversation_id: str) -> int:
        """How many times the backend side has been told to cancel its running turn.

        This is the backend's own count, not the system's. It is what separates a system
        that really stops its agent from one that merely records an interruption and
        leaves the agent running.
        """

    async def backend_model(self, conversation_id: str) -> str | None:
        """The model the backend side's session currently runs on — the backend's own
        account, which is what proves a carried model change actually reached it rather
        than being recorded and dropped. None when no session exists yet."""

    async def backend_reasoning_effort(self, conversation_id: str) -> str | None:
        """The reasoning effort the backend side's session currently runs on — the
        backend's own account. None when no session exists yet."""

    # Driving the backend. Each returns only after the implementation has fully
    # processed the event — see the timing obligation on this class.
    async def complete_running_turn(self, conversation_id: str) -> None:
        """The agent finished its turn, and the system has finished reacting to that,
        including dequeueing and delivering whatever was held behind it."""

    async def fail_running_turn(self, conversation_id: str) -> None:
        """The agent errored out of its turn, and the system has finished reacting to
        that, including dequeueing and delivering whatever was held behind it."""

    async def raise_permission_ask(self, conversation_id: str) -> str:
        """The agent asked for permission, and the system has finished reacting to that.
        Returns the ask's id."""

    async def answer_permission_ask(
        self, conversation_id: str, ask_id: str, option_id: str
    ) -> bool:
        """Answer an ask as the browser would, returning whether it landed. The system
        has finished reacting to it by the time this returns."""

    async def arm_backend_start_failure(self, conversation_id: str) -> None: ...

    async def arm_session_load_failure(self, conversation_id: str) -> None: ...

    async def arm_backend_write_failure(self, conversation_id: str) -> None:
        """Make writes to this conversation's backend fail.

        The armed failure persists: it keeps reproducing at the same boundary, for every
        later write, until the subject is torn down. A binder gets that from an agent
        scripted to reject prompts, not from breaking a pipe — a broken pipe usually
        takes the child with it, and the honest reason for the next attempt would then be
        ``backend_did_not_start``, which is a different failure at a different boundary.
        """

    # The system's own recording, normalised into the suite's vocabulary.
    async def recorded_facts(self, conversation_id: str) -> tuple[RecordedFact, ...]:
        """What the implementation recorded for this conversation, in order. An unknown
        conversation id has recorded nothing, so it returns empty rather than raising."""


FATE_TYPES = (
    PromptDeliveryStarted,
    PromptDeliveryQueued,
    PromptDeliveryInjected,
    PromptDeliveryRefused,
)

# No conformance test does real work, so anything approaching this limit is a subject
# that has wedged. The unit gate has no timeout of its own, and a hung process-backed
# subject would otherwise stall the whole run.
CONFORMANCE_TEST_TIME_LIMIT_SECONDS = 30.0


def _start_request(
    conversation_id: str,
    backend_key: ConversationBackendKey = ConversationBackendKey.hermes,
) -> ConversationStartRequest:
    return ConversationStartRequest(
        conversation_id=conversation_id, model="a-model", backend_key=backend_key
    )


def _facts_of_kind(
    facts: tuple[RecordedFact, ...], kind: RecordedFactKind
) -> tuple[RecordedFact, ...]:
    return tuple(fact for fact in facts if fact.kind is kind)


def _written_texts(writes: tuple[BackendWrite, ...]) -> tuple[str, ...]:
    return tuple(write.text for write in writes)


class ConversationContractConformanceSuite:
    """The conformance tests. Subclass this and provide a subject factory."""

    def open_system_under_test(self) -> AbstractAsyncContextManager[ConversationSystemUnderTest]:
        raise NotImplementedError

    def _run(self, exercise: Callable[[ConversationSystemUnderTest], Awaitable[None]]) -> None:
        async def main() -> None:
            async with self.open_system_under_test() as subject:
                await exercise(subject)

        asyncio.run(asyncio.wait_for(main(), CONFORMANCE_TEST_TIME_LIMIT_SECONDS))

    # --- A conversation is addressable immediately and only after creation ---

    def test_send_before_start_is_refused_and_nothing_else_raises(self) -> None:
        """Coverage 1."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            fate = await subject.system.send(
                "never-started",
                text_message_content("hello"),
                sender_label="owner",
            )
            assert fate == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.no_such_conversation
            )
            assert await subject.system.is_running("never-started") is False
            await subject.system.interrupt("never-started")

        self._run(exercise)

    def test_a_conversation_is_addressable_at_the_instant_start_returns(self) -> None:
        """Coverage 2."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            assert await subject.system.is_running("c") is False
            assert await subject.backend_writes("c") == ()
            fate = await subject.system.send(
                "c",
                text_message_content("first"),
                sender_label="owner",
            )
            assert fate == PromptDeliveryStarted()

        self._run(exercise)

    def test_starting_the_same_conversation_id_twice_raises(self) -> None:
        """Coverage 3."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            with pytest.raises(ConversationAlreadyStarted):
                await subject.system.start_conversation(_start_request("c"))

        self._run(exercise)

    def test_a_start_request_carrying_only_a_conversation_id_and_a_model_still_works(
        self,
    ) -> None:
        """Coverage 4."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                ConversationStartRequest(conversation_id="c", model="a-model")
            )
            fate = await subject.system.send(
                "c",
                text_message_content("first"),
                sender_label="owner",
            )
            assert fate == PromptDeliveryStarted()
            assert await subject.system.is_running("c") is True
            assert await subject.backend_writes("c") == (
                BackendWrite(
                    content=text_message_content("first"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.run_when_free,
                ),
            )

        self._run(exercise)

    # --- Every mode x idle/busy ---

    def test_run_when_free_while_idle_starts_the_turn(self) -> None:
        """Coverage 5."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            fate = await subject.system.send(
                "c",
                text_message_content("first"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
            )
            assert fate == PromptDeliveryStarted()
            assert await subject.system.is_running("c") is True

        self._run(exercise)

    def test_run_when_free_while_busy_queues_at_ascending_one_based_positions(self) -> None:
        """Coverage 6, including that a position is a place in the queue and not a count
        of everything ever queued."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            assert await subject.system.send(
                "c", text_message_content("incumbent"), sender_label="owner"
            ) == PromptDeliveryStarted()
            assert await subject.system.send(
                "c",
                text_message_content("held-a"),
                sender_label="owner",
            ) == (
                PromptDeliveryQueued(queue_position=1)
            )
            assert await subject.system.send(
                "c",
                text_message_content("held-b"),
                sender_label="owner",
            ) == (
                PromptDeliveryQueued(queue_position=2)
            )
            # Everything waiting leaves the queue and runs as one turn, so the next
            # message held is first in line — not third.
            await subject.complete_running_turn("c")
            assert await subject.system.send(
                "c",
                text_message_content("held-c"),
                sender_label="owner",
            ) == (
                PromptDeliveryQueued(queue_position=1)
            )

        self._run(exercise)

    def test_send_now_while_idle_behaves_exactly_like_the_default(self) -> None:
        """Coverage 7."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            fate = await subject.system.send(
                "c",
                text_message_content("first"),
                sender_label="owner",
                mode=PromptDeliveryMode.send_now,
            )
            assert fate == PromptDeliveryStarted()
            assert await subject.system.is_running("c") is True
            assert _written_texts(await subject.backend_writes("c")) == ("first",)
            facts = await subject.recorded_facts("c")
            assert _facts_of_kind(facts, RecordedFactKind.turn_ended) == ()

        self._run(exercise)

    def test_send_now_while_busy_starts_and_really_kills_the_incumbent(self) -> None:
        """Coverage 8, including that the incumbent's backend was actually cancelled."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            assert await subject.backend_cancellations("c") == 0
            fate = await subject.system.send(
                "c",
                text_message_content("urgent"),
                sender_label="owner",
                mode=PromptDeliveryMode.send_now,
            )
            assert fate == PromptDeliveryStarted()
            assert await subject.system.is_running("c") is True
            assert await subject.backend_cancellations("c") == 1
            endings = _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.turn_ended
            )
            assert len(endings) == 1
            assert endings[0].turn_ending is RecordedTurnEnding.interrupted

        self._run(exercise)

    def test_send_now_runs_next_and_held_prompts_keep_their_order_behind_it(self) -> None:
        """Coverage 9."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            assert await subject.system.send(
                "c",
                text_message_content("held-a"),
                sender_label="owner",
            ) == (
                PromptDeliveryQueued(queue_position=1)
            )
            assert await subject.system.send(
                "c",
                text_message_content("held-b"),
                sender_label="owner",
            ) == (
                PromptDeliveryQueued(queue_position=2)
            )
            assert await subject.system.send(
                "c",
                text_message_content("urgent"),
                sender_label="owner",
                mode=PromptDeliveryMode.send_now,
            ) == PromptDeliveryStarted()
            assert _written_texts(await subject.backend_writes("c")) == ("incumbent", "urgent")
            # The send-now went ahead of the line. What is left of the line keeps its
            # order and goes to the agent as one prompt.
            await subject.complete_running_turn("c")
            assert _written_texts(await subject.backend_writes("c")) == (
                "incumbent",
                "urgent",
                "owner:\nheld-a\n\nowner:\nheld-b",
            )

        self._run(exercise)

    def test_steer_into_a_running_hermes_turn_is_injected(self) -> None:
        """Coverage 10."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                _start_request("c", ConversationBackendKey.hermes)
            )
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            fate = await subject.system.send(
                "c",
                text_message_content("also consider this"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            assert fate == PromptDeliveryInjected()

        self._run(exercise)

    def test_steer_on_codex_and_claude_is_refused_running_or_idle(self) -> None:
        """Coverage 11."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            cannot_steer = PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.backend_cannot_steer
            )
            for backend_key in (ConversationBackendKey.codex, ConversationBackendKey.claude):
                idle_id = f"{backend_key}-idle"
                await subject.system.start_conversation(_start_request(idle_id, backend_key))
                assert await subject.system.send(
                    idle_id,
                    text_message_content("steered"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.steer,
                ) == cannot_steer

                running_id = f"{backend_key}-running"
                await subject.system.start_conversation(_start_request(running_id, backend_key))
                await subject.system.send(
                    running_id,
                    text_message_content("incumbent"),
                    sender_label="owner",
                )
                assert await subject.system.is_running(running_id) is True
                assert await subject.system.send(
                    running_id,
                    text_message_content("steered"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.steer,
                ) == cannot_steer

        self._run(exercise)

    def test_steer_while_idle_on_hermes_is_refused_for_want_of_a_running_turn(self) -> None:
        """Coverage 12."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                _start_request("c", ConversationBackendKey.hermes)
            )
            fate = await subject.system.send(
                "c",
                text_message_content("steered"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            assert fate == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.no_running_turn_to_steer_into
            )

        self._run(exercise)

    def test_steer_does_not_end_the_running_turn(self) -> None:
        """Coverage 13."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                _start_request("c", ConversationBackendKey.hermes)
            )
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send(
                "c",
                text_message_content("steered"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            assert await subject.system.is_running("c") is True
            facts = await subject.recorded_facts("c")
            assert _facts_of_kind(facts, RecordedFactKind.turn_ended) == ()
            assert await subject.backend_writes("c") == (
                BackendWrite(
                    content=text_message_content("incumbent"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.run_when_free,
                ),
                BackendWrite(
                    content=text_message_content("steered"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.steer,
                ),
            )

        self._run(exercise)

    def test_a_message_reaches_the_backend_whole_and_is_recorded_whole(self) -> None:
        """A message is what it holds, all the way through.

        Every piece the sender put in reaches the backend, in the order they were put in,
        and the record holds the same message rather than the words out of it. A system
        that carried only the words would pass every other exercise here and fail this
        one, which is the point of it.
        """

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            # Two runs of words rather than a picture, because a picture needs bytes
            # kept for it and this exercise is about the contract rather than about
            # files: a message of several pieces reaches the backend as several pieces
            # and is recorded as several pieces. A system that carried only the words
            # would join these into one and fail here. Where the bytes of a picture go
            # is proved where they are kept — over HTTP, per adapter, and in a browser.
            message = (
                MessageText(text="look at this"),
                MessageText(text="and tell me what it is"),
            )
            fate = await subject.system.send("c", message, sender_label="owner")

            assert fate == PromptDeliveryStarted()
            assert await subject.backend_writes("c") == (
                BackendWrite(
                    content=message,
                    sender_label="owner",
                    mode=PromptDeliveryMode.run_when_free,
                ),
            )
            delivered = _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.prompt_delivered
            )
            assert [fact.content for fact in delivered] == [message]

        self._run(exercise)

    # --- Fates are truthful, proved against the backend ---

    def test_started_means_the_text_is_on_the_wire_when_send_returns(self) -> None:
        """Coverage 14."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            fate = await subject.system.send(
                "c",
                text_message_content("the exact text"),
                sender_label="owner",
            )
            assert fate == PromptDeliveryStarted()
            assert await subject.backend_writes("c") == (
                BackendWrite(
                    content=text_message_content("the exact text"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.run_when_free,
                ),
            )

        self._run(exercise)

    def test_injected_means_the_steered_text_is_on_the_wire_when_send_returns(self) -> None:
        """Coverage 15."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                _start_request("c", ConversationBackendKey.hermes)
            )
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            fate = await subject.system.send(
                "c",
                text_message_content("the exact steer"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            assert fate == PromptDeliveryInjected()
            assert (await subject.backend_writes("c"))[-1] == BackendWrite(
                content=text_message_content("the exact steer"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )

        self._run(exercise)

    def test_queued_text_reaches_no_backend_before_or_after_settling(self) -> None:
        """Coverage 16."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            before = await subject.backend_writes("c")
            fate = await subject.system.send(
                "c",
                text_message_content("held"),
                sender_label="owner",
            )
            assert fate == PromptDeliveryQueued(queue_position=1)
            assert await subject.backend_writes("c") == before
            await subject.settle()
            assert await subject.backend_writes("c") == before
            assert "held" not in _written_texts(await subject.backend_writes("c"))

        self._run(exercise)

    def test_refused_text_never_reaches_the_backend_for_any_reason(self) -> None:
        """Coverage 17."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            assert await subject.system.send(
                "unknown", text_message_content("refused-text"), sender_label="owner"
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.no_such_conversation
            )
            assert await subject.backend_writes("unknown") == ()

            await subject.system.start_conversation(_start_request("no-start"))
            await subject.arm_backend_start_failure("no-start")
            assert await subject.system.send(
                "no-start", text_message_content("refused-text"), sender_label="owner"
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.backend_did_not_start
            )
            assert await subject.backend_writes("no-start") == ()

            await subject.system.start_conversation(_start_request("no-load"))
            await subject.arm_session_load_failure("no-load")
            assert await subject.system.send(
                "no-load", text_message_content("refused-text"), sender_label="owner"
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.session_did_not_load
            )
            assert await subject.backend_writes("no-load") == ()

            await subject.system.start_conversation(_start_request("no-write"))
            await subject.arm_backend_write_failure("no-write")
            assert await subject.system.send(
                "no-write", text_message_content("refused-text"), sender_label="owner"
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
            )
            assert await subject.backend_writes("no-write") == ()

            await subject.system.start_conversation(
                _start_request("idle-steer", ConversationBackendKey.hermes)
            )
            assert await subject.system.send(
                "idle-steer",
                text_message_content("refused-text"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.no_running_turn_to_steer_into
            )
            assert await subject.backend_writes("idle-steer") == ()

            await subject.system.start_conversation(
                _start_request("no-steer", ConversationBackendKey.codex)
            )
            await subject.system.send(
                "no-steer",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            assert await subject.system.send(
                "no-steer",
                text_message_content("refused-text"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.backend_cannot_steer
            )
            assert _written_texts(await subject.backend_writes("no-steer")) == ("incumbent",)

        self._run(exercise)

    def test_a_dequeued_delivery_records_its_own_fate_and_reaches_the_backend(self) -> None:
        """Coverage 18."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send(
                "c",
                text_message_content("held"),
                sender_label="automatic-loop",
            )
            await subject.complete_running_turn("c")
            assert await subject.recorded_facts("c") == (
                RecordedFact(
                    kind=RecordedFactKind.prompt_delivered,
                    content=text_message_content("incumbent"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.run_when_free,
                ),
                RecordedFact(
                    kind=RecordedFactKind.turn_ended,
                    turn_ending=RecordedTurnEnding.completed,
                ),
                RecordedFact(
                    kind=RecordedFactKind.prompt_delivered,
                    content=text_message_content("held"),
                    sender_label="automatic-loop",
                    mode=PromptDeliveryMode.run_when_free,
                ),
            )
            assert _written_texts(await subject.backend_writes("c")) == ("incumbent", "held")

        self._run(exercise)

    def test_a_dequeued_delivery_that_fails_records_a_refusal_and_the_drain_continues(
        self,
    ) -> None:
        """Coverage 19."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send("c", text_message_content("held-a"), sender_label="owner")
            await subject.system.send("c", text_message_content("held-b"), sender_label="owner")
            await subject.arm_backend_write_failure("c")
            await subject.complete_running_turn("c")
            refusals = _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.prompt_delivery_refused
            )
            assert tuple((fact.text, fact.refusal_reason) for fact in refusals) == (
                ("held-a", PromptDeliveryRefusalReason.write_to_backend_failed),
                ("held-b", PromptDeliveryRefusalReason.write_to_backend_failed),
            )
            assert _written_texts(await subject.backend_writes("c")) == ("incumbent",)
            assert await subject.system.is_running("c") is False

        self._run(exercise)

    def test_send_returns_before_the_turn_ends(self) -> None:
        """Coverage 20."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            fate = await subject.system.send(
                "c",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            assert fate == PromptDeliveryStarted()
            assert await subject.system.is_running("c") is True
            assert (
                _facts_of_kind(await subject.recorded_facts("c"), RecordedFactKind.turn_ended) == ()
            )

        self._run(exercise)

    # --- Busyness never refuses ---

    def test_ten_run_when_free_sends_against_a_busy_agent_all_queue(self) -> None:
        """Coverage 21."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            fates = [
                await subject.system.send(
                    "c",
                    text_message_content(f"held-{index}"),
                    sender_label="owner",
                )
                for index in range(1, 11)
            ]
            assert fates == [
                PromptDeliveryQueued(queue_position=position) for position in range(1, 11)
            ]

        self._run(exercise)

    def test_send_now_against_a_busy_agent_never_refuses(self) -> None:
        """Coverage 22."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            fates = [
                await subject.system.send(
                    "c",
                    text_message_content(f"urgent-{index}"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.send_now,
                )
                for index in range(1, 4)
            ]
            assert fates == [PromptDeliveryStarted()] * 3
            assert await subject.system.is_running("c") is True

        self._run(exercise)

    def test_every_refusal_reason_is_produced_only_by_its_own_cause(self) -> None:
        """Coverage 23."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            produced: dict[str, PromptDeliveryRefusalReason] = {}

            fate = await subject.system.send(
                "unknown",
                text_message_content("text"),
                sender_label="owner",
            )
            assert isinstance(fate, PromptDeliveryRefused)
            produced["never started"] = fate.refusal_reason

            await subject.system.start_conversation(_start_request("no-start"))
            await subject.arm_backend_start_failure("no-start")
            fate = await subject.system.send(
                "no-start",
                text_message_content("text"),
                sender_label="owner",
            )
            assert isinstance(fate, PromptDeliveryRefused)
            produced["backend will not spawn"] = fate.refusal_reason

            await subject.system.start_conversation(_start_request("no-load"))
            await subject.arm_session_load_failure("no-load")
            fate = await subject.system.send(
                "no-load",
                text_message_content("text"),
                sender_label="owner",
            )
            assert isinstance(fate, PromptDeliveryRefused)
            produced["session will not load"] = fate.refusal_reason

            await subject.system.start_conversation(_start_request("no-write"))
            await subject.arm_backend_write_failure("no-write")
            fate = await subject.system.send(
                "no-write",
                text_message_content("text"),
                sender_label="owner",
            )
            assert isinstance(fate, PromptDeliveryRefused)
            produced["write fails"] = fate.refusal_reason

            await subject.system.start_conversation(
                _start_request("idle-steer", ConversationBackendKey.hermes)
            )
            fate = await subject.system.send(
                "idle-steer",
                text_message_content("text"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            assert isinstance(fate, PromptDeliveryRefused)
            produced["steer with no running turn"] = fate.refusal_reason

            await subject.system.start_conversation(
                _start_request("no-steer", ConversationBackendKey.codex)
            )
            await subject.system.send(
                "no-steer",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            fate = await subject.system.send(
                "no-steer",
                text_message_content("text"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            assert isinstance(fate, PromptDeliveryRefused)
            produced["steer on a backend that cannot"] = fate.refusal_reason

            assert produced == {
                "never started": PromptDeliveryRefusalReason.no_such_conversation,
                "backend will not spawn": PromptDeliveryRefusalReason.backend_did_not_start,
                "session will not load": PromptDeliveryRefusalReason.session_did_not_load,
                "write fails": PromptDeliveryRefusalReason.write_to_backend_failed,
                "steer with no running turn": (
                    PromptDeliveryRefusalReason.no_running_turn_to_steer_into
                ),
                "steer on a backend that cannot": (
                    PromptDeliveryRefusalReason.backend_cannot_steer
                ),
            }
            assert set(produced.values()) == set(PromptDeliveryRefusalReason)

        self._run(exercise)

    # --- Each refusal reason arises only from its own cause ---

    def test_backend_did_not_start_comes_only_from_a_backend_that_will_not_spawn(self) -> None:
        """Coverage 24."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("armed"))
            await subject.arm_backend_start_failure("armed")
            assert await subject.system.send(
                "armed", text_message_content("text"), sender_label="owner"
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.backend_did_not_start
            )
            assert await subject.backend_writes("armed") == ()

            await subject.system.start_conversation(_start_request("unarmed"))
            assert await subject.system.send(
                "unarmed", text_message_content("text"), sender_label="owner"
            ) == PromptDeliveryStarted()

            await subject.system.start_conversation(_start_request("write-armed"))
            await subject.arm_backend_write_failure("write-armed")
            fate = await subject.system.send(
                "write-armed",
                text_message_content("text"),
                sender_label="owner",
            )
            assert isinstance(fate, PromptDeliveryRefused)
            assert fate.refusal_reason is not PromptDeliveryRefusalReason.backend_did_not_start

        self._run(exercise)

    def test_session_did_not_load_comes_only_from_a_session_that_will_not_load(self) -> None:
        """Coverage 25."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("armed"))
            await subject.arm_session_load_failure("armed")
            assert await subject.system.send(
                "armed", text_message_content("text"), sender_label="owner"
            ) == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.session_did_not_load
            )
            assert await subject.backend_writes("armed") == ()

            await subject.system.start_conversation(_start_request("unarmed"))
            assert await subject.system.send(
                "unarmed", text_message_content("text"), sender_label="owner"
            ) == PromptDeliveryStarted()

            await subject.system.start_conversation(_start_request("write-armed"))
            await subject.arm_backend_write_failure("write-armed")
            fate = await subject.system.send(
                "write-armed",
                text_message_content("text"),
                sender_label="owner",
            )
            assert isinstance(fate, PromptDeliveryRefused)
            assert fate.refusal_reason is not PromptDeliveryRefusalReason.session_did_not_load

        self._run(exercise)

    def test_write_to_backend_failed_arises_on_a_plain_send_and_on_a_steer(self) -> None:
        """Coverage 26."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            write_failed = PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
            )

            await subject.system.start_conversation(_start_request("plain"))
            await subject.arm_backend_write_failure("plain")
            assert (
                await subject.system.send(
                    "plain", text_message_content("text"), sender_label="owner"
                )
                == write_failed
            )
            assert await subject.backend_writes("plain") == ()
            assert await subject.system.is_running("plain") is False

            await subject.system.start_conversation(
                _start_request("steered", ConversationBackendKey.hermes)
            )
            await subject.system.send(
                "steered",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            await subject.arm_backend_write_failure("steered")
            assert await subject.system.send(
                "steered",
                text_message_content("text"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            ) == write_failed
            assert _written_texts(await subject.backend_writes("steered")) == ("incumbent",)
            assert await subject.system.is_running("steered") is True

        self._run(exercise)

    def test_a_refused_send_now_drains_the_queue_it_freed(self) -> None:
        """Coverage 27."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send("c", text_message_content("held-a"), sender_label="owner")
            await subject.system.send("c", text_message_content("held-b"), sender_label="owner")
            await subject.arm_backend_write_failure("c")
            fate = await subject.system.send(
                "c",
                text_message_content("urgent"),
                sender_label="owner",
                mode=PromptDeliveryMode.send_now,
            )
            assert fate == PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
            )
            facts = await subject.recorded_facts("c")
            assert tuple(fact.kind for fact in facts) == (
                RecordedFactKind.prompt_delivered,
                RecordedFactKind.turn_ended,
                RecordedFactKind.prompt_delivery_refused,
                RecordedFactKind.prompt_delivery_refused,
            )
            assert tuple(
                fact.text
                for fact in _facts_of_kind(facts, RecordedFactKind.prompt_delivery_refused)
            ) == ("held-a", "held-b")
            assert _written_texts(await subject.backend_writes("c")) == ("incumbent",)

        self._run(exercise)

    # --- Interrupt ---

    def test_interrupt_while_running_really_stops_the_agent(self) -> None:
        """Coverage 28, proved against the backend: only an interruption cancels it."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))

            await subject.system.send("c", text_message_content("first"), sender_label="owner")
            await subject.complete_running_turn("c")
            assert await subject.backend_cancellations("c") == 0

            await subject.system.send("c", text_message_content("second"), sender_label="owner")
            await subject.fail_running_turn("c")
            assert await subject.backend_cancellations("c") == 0

            await subject.system.send("c", text_message_content("third"), sender_label="owner")
            await subject.system.interrupt("c")
            assert await subject.backend_cancellations("c") == 1

            assert tuple(
                fact.turn_ending
                for fact in _facts_of_kind(
                    await subject.recorded_facts("c"), RecordedFactKind.turn_ended
                )
            ) == (
                RecordedTurnEnding.completed,
                RecordedTurnEnding.failed,
                RecordedTurnEnding.interrupted,
            )
            assert await subject.system.is_running("c") is False

        self._run(exercise)

    def test_interrupt_while_idle_records_nothing(self) -> None:
        """Coverage 29."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.interrupt("c")
            assert await subject.recorded_facts("c") == ()
            assert await subject.system.is_running("c") is False

        self._run(exercise)

    def test_interrupt_on_an_unknown_conversation_does_nothing(self) -> None:
        """Coverage 30."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.interrupt("unknown")
            assert await subject.recorded_facts("unknown") == ()

        self._run(exercise)

    def test_interrupt_sends_nothing_to_the_backend(self) -> None:
        """Coverage 31."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            before = await subject.backend_writes("c")
            await subject.system.interrupt("c")
            assert await subject.backend_writes("c") == before

        self._run(exercise)

    def test_interrupt_drains_the_held_queue(self) -> None:
        """Coverage 32."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send("c", text_message_content("held"), sender_label="owner")
            await subject.system.interrupt("c")
            assert _written_texts(await subject.backend_writes("c")) == ("incumbent", "held")
            assert await subject.system.is_running("c") is True

        self._run(exercise)

    # --- is_running truth through the lifecycle ---

    def test_is_running_through_the_whole_lifecycle(self) -> None:
        """Coverage 33."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            assert await subject.system.is_running("c") is False
            await subject.system.start_conversation(_start_request("c"))
            assert await subject.system.is_running("c") is False
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            assert await subject.system.is_running("c") is True
            ask_id = await subject.raise_permission_ask("c")
            assert await subject.system.is_running("c") is True
            assert await subject.answer_permission_ask("c", ask_id, "allow-once") is True
            await subject.complete_running_turn("c")
            assert await subject.system.is_running("c") is False

        self._run(exercise)

    def test_completing_a_turn_with_a_held_prompt_leaves_no_idle_gap(self) -> None:
        """Coverage 34."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send("c", text_message_content("held"), sender_label="owner")
            await subject.complete_running_turn("c")
            assert await subject.system.is_running("c") is True
            assert _written_texts(await subject.backend_writes("c")) == ("incumbent", "held")

        self._run(exercise)

    def test_is_running_is_false_after_a_failure_and_after_an_interrupt(self) -> None:
        """Coverage 35."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("failed"))
            await subject.system.send(
                "failed",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            await subject.fail_running_turn("failed")
            assert await subject.system.is_running("failed") is False

            await subject.system.start_conversation(_start_request("interrupted"))
            await subject.system.send(
                "interrupted",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            await subject.system.interrupt("interrupted")
            assert await subject.system.is_running("interrupted") is False

        self._run(exercise)

    # --- Sender label ---

    def test_the_sender_label_lands_on_every_prompt_fact(self) -> None:
        """Coverage 36."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                _start_request("c", ConversationBackendKey.hermes)
            )
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send(
                "c",
                text_message_content("held"),
                sender_label="automatic-loop",
            )
            await subject.system.send(
                "c",
                text_message_content("steered"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            await subject.complete_running_turn("c")
            delivered = _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.prompt_delivered
            )
            assert tuple((fact.text, fact.sender_label) for fact in delivered) == (
                ("incumbent", "owner"),
                ("steered", "owner"),
                ("held", "automatic-loop"),
            )

        self._run(exercise)

    # --- Permissions ---

    def test_a_permission_ask_is_recorded_and_the_turn_keeps_running(self) -> None:
        """Coverage 37."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            ask_id = await subject.raise_permission_ask("c")
            assert _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.permission_asked
            ) == (
                RecordedFact(
                    kind=RecordedFactKind.permission_asked, permission_ask_id=ask_id
                ),
            )
            assert await subject.system.is_running("c") is True

        self._run(exercise)

    def test_nothing_ever_answers_a_permission_ask_by_itself(self) -> None:
        """Coverage 38."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            ask_id = await subject.raise_permission_ask("c")
            await subject.settle()
            assert await subject.backend_permission_answer("c", ask_id) is None
            facts = await subject.recorded_facts("c")
            assert _facts_of_kind(facts, RecordedFactKind.permission_answered) == ()
            assert _facts_of_kind(facts, RecordedFactKind.turn_ended) == ()
            assert await subject.system.is_running("c") is True

        self._run(exercise)

    def test_a_pending_ask_holds_the_turn_open_until_it_is_answered(self) -> None:
        """Coverage 39, stated as what an outside observer can see: the ask waits, and
        the turn waits with it. How a subject enforces that is its own business."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            ask_id = await subject.raise_permission_ask("c")

            await subject.settle()
            assert await subject.system.is_running("c") is True
            assert (
                _facts_of_kind(await subject.recorded_facts("c"), RecordedFactKind.turn_ended) == ()
            )

            assert await subject.answer_permission_ask("c", ask_id, "allow-once") is True
            await subject.complete_running_turn("c")
            assert await subject.system.is_running("c") is False
            assert tuple(
                fact.turn_ending
                for fact in _facts_of_kind(
                    await subject.recorded_facts("c"), RecordedFactKind.turn_ended
                )
            ) == (RecordedTurnEnding.completed,)

        self._run(exercise)

    def test_an_answered_permission_ask_resolves_and_frees_the_turn(self) -> None:
        """Coverage 40."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            ask_id = await subject.raise_permission_ask("c")
            assert await subject.answer_permission_ask("c", ask_id, "allow-once") is True
            assert await subject.backend_permission_answer("c", ask_id) == "allow-once"
            assert _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.permission_answered
            ) == (
                RecordedFact(
                    kind=RecordedFactKind.permission_answered, permission_ask_id=ask_id
                ),
            )
            await subject.complete_running_turn("c")
            assert await subject.system.is_running("c") is False
            assert _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.turn_ended
            ) == (
                RecordedFact(
                    kind=RecordedFactKind.turn_ended,
                    turn_ending=RecordedTurnEnding.completed,
                ),
            )

        self._run(exercise)

    def test_an_answer_lands_only_on_a_pending_ask_of_the_live_turn(self) -> None:
        """Coverage 41."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("live"))
            await subject.system.send(
                "live",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            ask_id = await subject.raise_permission_ask("live")

            assert await subject.answer_permission_ask("live", "no-such-ask", "allow") is False
            assert await subject.backend_permission_answer("live", "no-such-ask") is None
            assert _facts_of_kind(
                await subject.recorded_facts("live"), RecordedFactKind.permission_answered
            ) == ()

            assert await subject.answer_permission_ask("live", ask_id, "allow-once") is True
            facts_after_the_real_answer = await subject.recorded_facts("live")
            assert await subject.answer_permission_ask("live", ask_id, "deny") is False
            assert await subject.backend_permission_answer("live", ask_id) == "allow-once"
            assert await subject.recorded_facts("live") == facts_after_the_real_answer

            await subject.system.start_conversation(_start_request("dead"))
            await subject.system.send(
                "dead",
                text_message_content("incumbent"),
                sender_label="owner",
            )
            dead_ask_id = await subject.raise_permission_ask("dead")
            await subject.system.interrupt("dead")
            assert await subject.answer_permission_ask("dead", dead_ask_id, "allow") is False
            assert await subject.backend_permission_answer("dead", dead_ask_id) is None
            assert _facts_of_kind(
                await subject.recorded_facts("dead"), RecordedFactKind.permission_answered
            ) == ()

        self._run(exercise)

    # --- Turn endings are events, never return values ---

    def test_turn_endings_are_recorded_facts_and_never_returned_fates(self) -> None:
        """Coverage 42."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            returned_fates = []

            await subject.system.start_conversation(_start_request("completed"))
            returned_fates.append(
                await subject.system.send(
                    "completed",
                    text_message_content("incumbent"),
                    sender_label="owner",
                )
            )
            await subject.complete_running_turn("completed")

            await subject.system.start_conversation(_start_request("failed"))
            returned_fates.append(
                await subject.system.send(
                    "failed",
                    text_message_content("incumbent"),
                    sender_label="owner",
                )
            )
            await subject.fail_running_turn("failed")

            await subject.system.start_conversation(_start_request("interrupted"))
            returned_fates.append(
                await subject.system.send(
                    "interrupted",
                    text_message_content("incumbent"),
                    sender_label="owner",
                )
            )
            returned_fates.append(
                await subject.system.send(
                    "interrupted",
                    text_message_content("held"),
                    sender_label="owner",
                )
            )
            await subject.system.interrupt("interrupted")

            expected_endings = {
                "completed": RecordedTurnEnding.completed,
                "failed": RecordedTurnEnding.failed,
                "interrupted": RecordedTurnEnding.interrupted,
            }
            for conversation_id, ending in expected_endings.items():
                endings = _facts_of_kind(
                    await subject.recorded_facts(conversation_id), RecordedFactKind.turn_ended
                )
                assert tuple(fact.turn_ending for fact in endings) == (ending,)

            assert [type(fate) for fate in returned_fates] == [
                PromptDeliveryStarted,
                PromptDeliveryStarted,
                PromptDeliveryStarted,
                PromptDeliveryQueued,
            ]
            for fate in returned_fates:
                assert isinstance(fate, FATE_TYPES)
            assert {
                fate_type.__name__: tuple(field.name for field in fields(fate_type))
                for fate_type in FATE_TYPES
            } == {
                "PromptDeliveryStarted": (),
                "PromptDeliveryQueued": ("queue_position",),
                "PromptDeliveryInjected": (),
                "PromptDeliveryRefused": ("refusal_reason",),
            }

        self._run(exercise)

    # --- A send can carry a model or reasoning-effort change (commit-on-send) ---

    def test_a_send_carrying_a_model_change_changes_the_model_from_that_delivery_on(
        self,
    ) -> None:
        """Coverage 43: the change lands with the delivery, reaches the backend side,
        persists for later sends, and is recorded."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                ConversationStartRequest(conversation_id="c", model="first-model")
            )
            fate = await subject.system.send(
                "c",
                text_message_content("switch here"),
                sender_label="owner",
                model_change="second-model",
            )
            assert isinstance(fate, PromptDeliveryStarted)
            assert await subject.backend_model("c") == "second-model"
            changes = _facts_of_kind(
                await subject.recorded_facts("c"), RecordedFactKind.model_changed
            )
            assert tuple(fact.model for fact in changes) == ("second-model",)

            await subject.complete_running_turn("c")
            await subject.system.send(
                "c",
                text_message_content("plain send after"),
                sender_label="owner",
            )
            assert await subject.backend_model("c") == "second-model"

        self._run(exercise)

    def test_a_send_without_a_change_leaves_model_and_effort_alone(self) -> None:
        """Coverage 44: absent means the conversation stays on what it is."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                ConversationStartRequest(
                    conversation_id="c", model="start-model", reasoning_effort="start-effort"
                )
            )
            await subject.system.send("c", text_message_content("plain"), sender_label="owner")
            assert await subject.backend_model("c") == "start-model"
            assert await subject.backend_reasoning_effort("c") == "start-effort"
            facts = await subject.recorded_facts("c")
            assert _facts_of_kind(facts, RecordedFactKind.model_changed) == ()

        self._run(exercise)

    def test_a_change_held_behind_a_busy_agent_lands_when_its_message_runs(self) -> None:
        """Coverage 45: the change rides the message, not the moment of sending."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                ConversationStartRequest(conversation_id="c", model="start-model")
            )
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            fate = await subject.system.send(
                "c",
                text_message_content("held with change"),
                sender_label="owner",
                model_change="next-model",
            )
            assert isinstance(fate, PromptDeliveryQueued)
            assert await subject.backend_model("c") == "start-model"

            await subject.complete_running_turn("c")
            assert await subject.backend_model("c") == "next-model"
            assert "held with change" in _written_texts(await subject.backend_writes("c"))

        self._run(exercise)

    def test_a_refused_delivery_carrying_a_change_changes_nothing(self) -> None:
        """Coverage 46: the change lands with the delivery, so no delivery, no change."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                ConversationStartRequest(conversation_id="c", model="start-model")
            )
            await subject.system.send(
                "c",
                text_message_content("make the session exist"),
                sender_label="owner",
            )
            await subject.complete_running_turn("c")
            await subject.arm_backend_write_failure("c")

            fate = await subject.system.send(
                "c",
                text_message_content("doomed"),
                sender_label="owner",
                model_change="never-model",
            )
            assert isinstance(fate, PromptDeliveryRefused)
            assert fate.refusal_reason is PromptDeliveryRefusalReason.write_to_backend_failed
            assert await subject.backend_model("c") == "start-model"
            facts = await subject.recorded_facts("c")
            assert _facts_of_kind(facts, RecordedFactKind.model_changed) == ()

        self._run(exercise)

    def test_the_pending_ask_read_tracks_the_ask_through_its_whole_life(self) -> None:
        """Coverage 48: False before and after — pending only while an unanswered ask
        sits on the live turn, and an ask dies with its turn."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            assert not await subject.system.has_pending_permission_ask("never-started")

            await subject.system.start_conversation(_start_request("c"))
            assert not await subject.system.has_pending_permission_ask("c")

            await subject.system.send("c", text_message_content("work"), sender_label="owner")
            assert not await subject.system.has_pending_permission_ask("c")

            ask_id = await subject.raise_permission_ask("c")
            assert await subject.system.has_pending_permission_ask("c")

            assert await subject.answer_permission_ask("c", ask_id, "allow")
            assert not await subject.system.has_pending_permission_ask("c")

            await subject.raise_permission_ask("c")
            assert await subject.system.has_pending_permission_ask("c")
            await subject.system.interrupt("c")
            assert not await subject.system.has_pending_permission_ask("c")

        self._run(exercise)

    def test_kill_stops_the_turn_and_discards_every_held_message(self) -> None:
        """Coverage 49: kill silences the running turn AND the queue — the agent is
        really cancelled, nothing held ever reaches the backend, each discard is
        recorded, and a pending ask dies with the killed turn."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(_start_request("c"))
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            await subject.system.send("c", text_message_content("held one"), sender_label="owner")
            await subject.system.send("c", text_message_content("held two"), sender_label="owner")
            await subject.raise_permission_ask("c")

            await subject.system.kill("c")
            await subject.settle()

            assert not await subject.system.is_running("c")
            assert not await subject.system.has_pending_permission_ask("c")
            assert await subject.backend_cancellations("c") == 1
            written = _written_texts(await subject.backend_writes("c"))
            assert "held one" not in written and "held two" not in written

            facts = await subject.recorded_facts("c")
            discards = _facts_of_kind(facts, RecordedFactKind.prompt_discarded)
            assert tuple(fact.text for fact in discards) == ("held one", "held two")
            endings = _facts_of_kind(facts, RecordedFactKind.turn_ended)
            assert tuple(fact.turn_ending for fact in endings) == (
                RecordedTurnEnding.interrupted,
            )

        self._run(exercise)

    def test_kill_is_not_the_end_of_the_conversation(self) -> None:
        """Coverage 50: killed is not closed — the conversation stays addressable and a
        later send starts a turn exactly as always. Kill on idle-with-nothing-held and
        on an unknown id are no-ops."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.kill("never-started")

            await subject.system.start_conversation(_start_request("c"))
            await subject.system.kill("c")

            await subject.system.send("c", text_message_content("work"), sender_label="owner")
            await subject.system.kill("c")
            fate = await subject.system.send(
                "c",
                text_message_content("after the kill"),
                sender_label="owner",
            )
            assert isinstance(fate, PromptDeliveryStarted)
            assert await subject.system.is_running("c")
            assert "after the kill" in _written_texts(await subject.backend_writes("c"))

        self._run(exercise)

    def test_a_steer_cannot_carry_a_change(self) -> None:
        """Coverage 47: the turn a steer joins is already running — caller error, not
        a delivery fate."""

        async def exercise(subject: ConversationSystemUnderTest) -> None:
            await subject.system.start_conversation(
                _start_request("c", backend_key=ConversationBackendKey.hermes)
            )
            await subject.system.send("c", text_message_content("incumbent"), sender_label="owner")
            with pytest.raises(ValueError):
                await subject.system.send(
                    "c",
                    text_message_content("steered"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.steer,
                    model_change="other-model",
                )
            assert await subject.system.is_running("c")

        self._run(exercise)
