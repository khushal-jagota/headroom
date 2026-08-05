"""The conversation system: SQLite for the record, one child process per conversation.

This is the real implementation of ``ConversationSystem``. Everything the contract says
lives here, and nothing about any vendor's protocol does — that is behind the backend
adapter seam in ``backends/contracts.py``.

Three ideas hold the concurrency together.

**One lock per conversation, and no wire I/O under it.** A send takes the lock only long
enough to decide what it is doing and to reserve the turn it is about to start. It lets go
while it talks to the child — spawning can take seconds — and takes it back to finish:
write the rows, mark the turn running. Everything that decides anything about a
conversation goes through that lock, so two callers never both think the agent is free.

**A turn token, minted by the core.** A turn is named when it is reserved, and the adapter
puts that name on everything it later reports. A turn the core has already ended is over:
its late news names a turn that is gone and is dropped. Endings are once per turn and the
first one wins, whoever caused it.

**One queue per conversation for backend news, worked through in order.** A turn's news
cannot land before the send that started it has finished writing its rows, so the queue
waits for a turn it has not seen finish reserving. That is the whole reason a turn can be
"reserved" as well as "running": between the two, the text is on the wire and the record
does not know about it yet, and an agent that answers that fast must still queue behind
its own prompt.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable, Coroutine, Mapping
from contextlib import suppress
from dataclasses import dataclass, field, replace
from enum import StrEnum
from functools import partial
from typing import Any
from uuid import uuid4

from planner.conversation.backend_lifecycle import BackendLifecycleCoordinator
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendUserInputRequest,
    NeedsRebind,
    PermissionAnswerWriteFailed,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
    UserInputAnswerWriteFailed,
)
from planner.conversation.contracts import (
    AgentCommand,
    ConversationBackendKey,
    ConversationStartRequest,
    HeldPrompt,
    HeldPromptPromotionFate,
    HeldPromptPromotionMode,
    PromptDeliveryFate,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    backend_supports_steer,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AgentMessageEventPayload,
    ContextCompactedEventPayload,
    ConversationEventPayload,
    ConversationLiveTailFrame,
    ConversationTurnEnding,
    HeldPromptsChangedFrame,
    ModelChangedEventPayload,
    ModelThinkingFrame,
    PermissionAnsweredEventPayload,
    PermissionAskedEventPayload,
    PlanEntry,
    PlanUpdatedEventPayload,
    PromptDeliveryRefusedEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    TokenUsageEventPayload,
    ToolCallFinishedEventPayload,
    ToolCallProgressFrame,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
    UserInputAnswer,
    UserInputAnsweredEventPayload,
    UserInputFailedEventPayload,
    UserInputQuestion,
    UserInputRequestedEventPayload,
)
from planner.conversation.live_tail import ConversationLiveTail
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import (
    MessageContent,
    prefix_message_content_text,
    require_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import (
    ConversationRecord,
    ConversationRecordNamesNoModel,
    ConversationStore,
    StoredConversationEvent,
)

LOGGER = logging.getLogger("planner.conversation")

# How long a child may sit doing nothing before it is stopped, and how often that is
# checked. A stopped child costs a conversation nothing: its session cursor is stored, so
# the next message resumes it without saying anything about the gap.
IDLE_CHILD_STOP_AFTER_SECONDS = 30 * 60
IDLE_CHILD_SWEEP_INTERVAL_SECONDS = 5 * 60

# How much of a failed turn's standard error goes in the error-log line. Enough to see
# what happened, not enough to bury the line it is part of.
STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS = 2048

# How often a conversation may say that its model is thinking. Reasoning arrives in a
# burst of many small pieces and they all mean one thing — the agent is alive and working
# — so telling a browser hundreds of times would be hundreds of frames saying the same
# thing. Dropping the rest costs nothing: a pulse carries no information to lose.
MODEL_THINKING_PULSE_INTERVAL_SECONDS = 0.25

# What separates a conversation's role text from the first message it is composed onto.
ROLE_TEXT_PROMPT_SEPARATOR = "\n\n"


class _ConversationPhase(StrEnum):
    """What a conversation is in the middle of. One phase, one owner.

    ``idle`` and ``running`` are the resting phases: nobody is part-way through anything,
    and a caller that needs the conversation to hold still can act. The other three each
    say that somebody already owns the conversation — a send is on the wire, an ending is
    being recorded, or the held queue is being worked through — and that a caller who
    wants to kill or replace the running turn has to wait for them to finish.
    """

    idle = "idle"
    starting = "starting"
    running = "running"
    ending = "ending"
    draining = "draining"


@dataclass(slots=True)
class _ReservedTurn:
    """A turn that has been named and is being written to the wire.

    ``resolved`` is set once its fate is known, whether it became the running turn or died
    on the way. The backend event queue waits on it rather than guessing.
    """

    token: TurnToken
    resolved: asyncio.Event


@dataclass(slots=True)
class _RunningTurn:
    token: TurnToken
    pending_permission_ask_ids: set[str] = field(default_factory=set)
    pending_user_input_questions: dict[str, tuple[UserInputQuestion, ...]] = field(
        default_factory=dict
    )
    ended: bool = False
    # Set when the core is part-way through ending this turn itself — an interrupt, or a
    # send-now killing the incumbent. The cancel goes out with the lock let go, and the
    # backend reports the ending it was just asked for while that is happening. Only one
    # of the two may be recorded, and it is the core's: the core is the side that knows
    # the turn was interrupted rather than merely over.
    ending_is_the_cores: bool = False


@dataclass(frozen=True, slots=True)
class _HeldPrompt:
    """A message waiting for the agent to free up, with the change it carries.

    The sender's own id and send instant wait here with it: a held message is delivered,
    refused or discarded long after the caller has gone, and whichever row it becomes has
    to carry the same id the sender minted.
    """

    held_prompt_id: str
    content: MessageContent
    sender_label: str
    model_change: str | None
    reasoning_effort_change: str | None
    sender_message_id: str | None
    sent_at_unix_milliseconds: int | None
    snapshot_sent_at_unix_milliseconds: int


@dataclass(frozen=True, slots=True)
class _PromptDeliveryAttempt:
    """The wire result and whether a refusal must remain after its caller leaves.

    A normal direct refusal is returned to the caller. A refusal after replacement of an
    already-failed child is also recorded because recovery is the durable outcome.
    """

    refusal_reason: PromptDeliveryRefusalReason | None = None
    record_refusal: bool = False


type _BackendEventHandler = Callable[[], Coroutine[Any, Any, None]]


@dataclass(slots=True)
class _ConversationState:
    """Everything this process knows about one conversation.

    ``record`` mirrors the conversation's stored row and is replaced whenever the row
    moves, so there is one answer in memory rather than a row and a drifting copy of it.
    """

    record: ConversationRecord
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phase: _ConversationPhase = _ConversationPhase.idle
    phase_settled: asyncio.Event = field(default_factory=asyncio.Event)
    held_prompts: deque[_HeldPrompt] = field(default_factory=deque)
    backend_event_queue: asyncio.Queue[_BackendEventHandler] = field(
        default_factory=asyncio.Queue
    )
    backend_event_pump: asyncio.Task[None] | None = None
    reserved_turn: _ReservedTurn | None = None
    running_turn: _RunningTurn | None = None
    last_ended_turn_token: TurnToken | None = None
    child: BackendChild | None = None
    next_turn_number: int = 1
    has_delivered_prompt: bool = False
    last_touched_monotonic: float = 0.0
    # When this conversation last said its model was thinking. Kept per conversation
    # because the rate it may be said at is about one browser watching one conversation.
    model_thinking_shown_at_monotonic: float = 0.0

    def __post_init__(self) -> None:
        self.phase_settled.set()


class SqliteProcessConversationSystem:
    """The conversation system Panels runs on.

    It is handed the store its record lives in and one child factory per backend key. It
    spawns nothing until a conversation has something to send, and it re-attaches to
    nothing when the process starts: after a restart no turn is running anywhere, and
    ``is_running`` says so.
    """

    def __init__(
        self,
        *,
        store: ConversationStore,
        backend_child_factories: Mapping[ConversationBackendKey, BackendChildFactory],
        message_files: ConversationMessageFiles,
        live_tail: ConversationLiveTail | None = None,
        monotonic_now: Callable[[], float] = time.monotonic,
        idle_child_stop_after_seconds: float = IDLE_CHILD_STOP_AFTER_SECONDS,
        idle_child_sweep_interval_seconds: float = IDLE_CHILD_SWEEP_INTERVAL_SECONDS,
        backend_lifecycle: BackendLifecycleCoordinator | None = None,
    ) -> None:
        missing = sorted(set(ConversationBackendKey) - set(backend_child_factories))
        if missing:
            raise ValueError(f"no backend child factory for {missing}")
        self._store = store
        self._message_files = message_files
        # Nobody watching is the ordinary case for a system built without one: the record
        # is written exactly the same way, and there is simply nowhere to show it.
        self._live_tail = live_tail
        self._backend_child_factories = dict(backend_child_factories)
        self._backend_lifecycle = backend_lifecycle
        self._monotonic_now = monotonic_now
        self._idle_child_stop_after_seconds = idle_child_stop_after_seconds
        self._idle_child_sweep_interval_seconds = idle_child_sweep_interval_seconds
        self._conversations: dict[str, _ConversationState] = {}
        self._conversations_lock = asyncio.Lock()
        self._idle_child_janitor: asyncio.Task[None] | None = None

    # --- the contract -------------------------------------------------------------------

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        """Create the conversation. Writing its row is the first thing that happens.

        The row exists the moment it is committed, so a message can arrive for this
        conversation before this call has finished — and that message will have picked the
        conversation up from its row, and may already have a child running. Which is why
        what follows only fills a gap: whatever is there is what this process is using, and
        replacing it would strand a live child that nothing can reach any more.
        """
        resolved = resolve_conversation_start_request(request)
        record = await self._store.create_conversation(resolved)
        async with self._conversations_lock:
            if record.conversation_id not in self._conversations:
                self._conversations[record.conversation_id] = _ConversationState(record=record)

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
        """Send a message in. See the contract; the two sender-minted fields are extra.

        ``sender_message_id`` and ``sent_at_unix_milliseconds`` are the sender's own facts
        about this message and are stored on its row exactly as they were given. A sender
        that mints neither — every caller inside Panels today — leaves both absent and
        nothing about its rows changes.
        """
        if mode is PromptDeliveryMode.steer and (
            model_change is not None or reasoning_effort_change is not None
        ):
            raise ValueError(
                "a steer cannot carry a model or reasoning-effort change: the turn it "
                "joins is already running"
            )
        require_message_content(content)

        state = await self._conversation_state(conversation_id)
        if state is None:
            return PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.no_such_conversation
            )
        state.last_touched_monotonic = self._monotonic_now()

        if mode is PromptDeliveryMode.steer:
            return await self._steer(
                state, content, sender_label, sender_message_id, sent_at_unix_milliseconds
            )
        if mode is PromptDeliveryMode.send_now:
            return await self._send_now(
                state,
                content,
                sender_label,
                model_change,
                reasoning_effort_change,
                sender_message_id,
                sent_at_unix_milliseconds,
            )
        return await self._run_when_free(
            state,
            content,
            sender_label,
            model_change,
            reasoning_effort_change,
            sender_message_id,
            sent_at_unix_milliseconds,
        )

    async def interrupt(self, conversation_id: str) -> None:
        state = await self._conversation_state(conversation_id)
        if state is None:
            return
        state.last_touched_monotonic = self._monotonic_now()
        await self._acquire_settled(state)
        try:
            running = state.running_turn
            if running is None:
                return
            try:
                await self._cancel_and_end_running_turn(state, running)
            finally:
                self._settle_phase(state)
        finally:
            state.lock.release()
        await self._drain_held_prompts(state)

    async def kill(self, conversation_id: str) -> None:
        """Stop the running turn and throw away everything that was waiting behind it.

        Unlike an interrupt, this is the end of the conversation's traffic: the queue is
        emptied rather than let run, so nothing happens afterwards until someone sends
        again. Each discarded message is written down before the turn's ending, because
        text a caller handed over must never disappear without a trace.

        The agent goes too. A killed conversation is one nobody is coming back to, and a
        child left running is an agent working in somebody's folder that nothing will speak
        to again — it would sit there until the idle sweep noticed. Whether a turn was
        running makes no difference to that.

        The whole of it happens under the conversation's lock, including the cancel — the
        one place wire I/O is held under it, because being one act is the point. A queue
        that could take on a message between the turn dying and the queue emptying would
        leave something running after a kill, which is what a kill is for stopping.
        """
        state = await self._conversation_state(conversation_id)
        if state is None:
            return
        state.last_touched_monotonic = self._monotonic_now()
        await self._acquire_settled(state)
        try:
            await self._discard_held_prompts(state)
            running = state.running_turn
            try:
                child = state.child
                if child is not None:
                    if running is not None:
                        await self._cancel_child_turn(state, child)
                    if state.child is child:
                        await self._discard_child(state, child)
                if running is not None:
                    await self._end_turn(
                        state, running, ConversationTurnEnding.interrupted, None
                    )
            finally:
                self._settle_phase(state)
        finally:
            state.lock.release()

    async def is_running(self, conversation_id: str) -> bool:
        """Whether a turn is running right now.

        Read without taking the conversation's lock, on purpose. The lock is let go while a
        send is on the wire, so holding it would not make this read any more current — it
        would only make a question the browser asks constantly wait behind a spawn.
        """
        state = await self._conversation_state(conversation_id)
        return state is not None and state.running_turn is not None

    async def has_pending_permission_ask(self, conversation_id: str) -> bool:
        state = await self._conversation_state(conversation_id)
        if state is None or state.running_turn is None:
            return False
        return bool(state.running_turn.pending_permission_ask_ids)

    async def pending_permission_ask_ids(self, conversation_id: str) -> frozenset[str]:
        """Which asks are waiting for an answer right now.

        The same fact ``has_pending_permission_ask`` answers, named rather than counted,
        so a surface that has to show the ask itself can find it in the record instead of
        working out for itself which one is still live.
        """
        state = await self._conversation_state(conversation_id)
        if state is None or state.running_turn is None:
            return frozenset()
        return frozenset(state.running_turn.pending_permission_ask_ids)

    async def has_pending_user_input(self, conversation_id: str) -> bool:
        state = await self._conversation_state(conversation_id)
        if state is None or state.running_turn is None:
            return False
        return bool(state.running_turn.pending_user_input_questions)

    async def pending_user_input_request_ids(self, conversation_id: str) -> frozenset[str]:
        state = await self._conversation_state(conversation_id)
        if state is None or state.running_turn is None:
            return frozenset()
        return frozenset(state.running_turn.pending_user_input_questions)

    async def held_prompts(self, conversation_id: str) -> tuple[HeldPrompt, ...]:
        """Return a stable FIFO snapshot of the messages waiting in this process."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            return ()
        async with state.lock:
            return tuple(
                HeldPrompt(
                    held_prompt_id=held.held_prompt_id,
                    content=held.content,
                    sender_label=held.sender_label,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.snapshot_sent_at_unix_milliseconds,
                )
                for held in state.held_prompts
            )

    async def promote_held_prompt(
        self,
        conversation_id: str,
        held_prompt_id: str,
        mode: HeldPromptPromotionMode,
    ) -> HeldPromptPromotionFate | None:
        """Atomically claim one held message and deliver it in the selected mode."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            return None
        state.last_touched_monotonic = self._monotonic_now()

        await self._acquire_settled(state)
        held: _HeldPrompt | None = None
        reservation: _ReservedTurn | None = None
        steer_child: BackendChild | None = None
        immediate_refusal: PromptDeliveryRefusalReason | None = None
        try:
            position = next(
                (
                    index
                    for index, candidate in enumerate(state.held_prompts)
                    if candidate.held_prompt_id == held_prompt_id
                ),
                None,
            )
            if position is None:
                return None
            held = state.held_prompts[position]

            if mode is HeldPromptPromotionMode.send_now:
                running = state.running_turn
                if running is not None:
                    try:
                        await self._cancel_and_end_running_turn(state, running)
                    except BaseException:
                        self._settle_phase(state)
                        raise
                del state.held_prompts[position]
                self._publish_held_prompts_changed(state)
                reservation = self._reserve_turn(state)
            else:
                del state.held_prompts[position]
                self._publish_held_prompts_changed(state)
                if not backend_supports_steer(state.record.backend_key):
                    immediate_refusal = PromptDeliveryRefusalReason.backend_cannot_steer
                elif state.running_turn is None or state.child is None:
                    immediate_refusal = (
                        PromptDeliveryRefusalReason.no_running_turn_to_steer_into
                    )
                else:
                    steer_child = state.child

                if immediate_refusal is not None:
                    await self._record_promoted_refusal(
                        state,
                        held,
                        PromptDeliveryMode.steer,
                        immediate_refusal,
                    )
        finally:
            state.lock.release()

        assert held is not None
        if mode is HeldPromptPromotionMode.send_now:
            assert reservation is not None
            try:
                delivery = await self._deliver_prompt(
                    state,
                    reservation.token,
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=PromptDeliveryMode.send_now,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                )
                started = await self._finalize_delivery(
                    state,
                    reservation,
                    delivery.refusal_reason,
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=PromptDeliveryMode.send_now,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    record_refusal=True,
                    phase_when_not_started=_ConversationPhase.idle,
                )
            except BaseException:
                self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
                raise
            if started:
                return PromptDeliveryStarted()
            refusal = delivery.refusal_reason
            assert refusal is not None
            await self._drain_held_prompts(state)
            return PromptDeliveryRefused(refusal_reason=refusal)

        if immediate_refusal is not None:
            await self._drain_held_prompts(state)
            return PromptDeliveryRefused(refusal_reason=immediate_refusal)

        assert steer_child is not None
        try:
            await steer_child.steer(held.content, sender_label=held.sender_label)
        except PromptWriteFailed:
            refusal = PromptDeliveryRefusalReason.write_to_backend_failed
            async with state.lock:
                await self._record_promoted_refusal(
                    state, held, PromptDeliveryMode.steer, refusal
                )
            await self._drain_held_prompts(state)
            return PromptDeliveryRefused(refusal_reason=refusal)

        async with state.lock:
            await self._append_event(
                state,
                PromptEventPayload(
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=PromptDeliveryMode.steer,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                ),
            )
        return PromptDeliveryInjected()

    async def discard_held_prompt(self, conversation_id: str, held_prompt_id: str) -> bool:
        """Throw away one message that is waiting, and say whether there was one to throw.

        A held message is this system's own and nothing else's: it has never been written
        to a backend, so taking it out of the queue reaches nothing and undoes nothing.
        It is still written down as discarded — the same row a kill writes — because text
        somebody handed over must never disappear without a trace.

        It is addressed by the id the server gave the held queue entry.

        Not finding it is an ordinary answer rather than an error. A held message runs the
        moment the agent frees up, so the one somebody is looking at may already have
        gone — and being told so is the truth, not a failure.
        """
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        state.last_touched_monotonic = self._monotonic_now()

        async with state.lock:
            for position, held in enumerate(state.held_prompts):
                if held.held_prompt_id != held_prompt_id:
                    continue
                # Out of the queue before its row is written, so a write that falls over
                # leaves the message discarded rather than delivered.
                del state.held_prompts[position]
                self._publish_held_prompts_changed(state)
                await self._append_event(
                    state,
                    PromptDiscardedEventPayload(
                        content=held.content,
                        sender_label=held.sender_label,
                        sender_message_id=held.sender_message_id,
                    ),
                )
                return True
        return False

    # --- answering a permission ask -----------------------------------------------------

    async def answer_permission_ask(
        self, conversation_id: str, ask_id: str, option_id: str
    ) -> bool:
        """Give the backend the option a person chose, and say whether it landed.

        It lands only if the ask is still pending on the turn that is live now *and* the
        backend took the answer. Answering an unknown ask, an already-answered ask, or an
        ask whose turn has ended changes nothing and records nothing. An answer the backend
        would not take leaves the ask waiting, so it can be given again.
        """
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        state.last_touched_monotonic = self._monotonic_now()

        async with state.lock:
            running = state.running_turn
            child = state.child
            if running is None or child is None:
                return False
            if ask_id not in running.pending_permission_ask_ids:
                return False
            # Taken out of the pending set before the wire write, so a second answer
            # arriving at the same time finds nothing to answer.
            running.pending_permission_ask_ids.discard(ask_id)
            answered_turn_token = running.token

        reached_the_backend = True
        try:
            await child.answer_permission_ask(ask_id, option_id)
        except PermissionAnswerWriteFailed:
            reached_the_backend = False

        async with state.lock:
            running = state.running_turn
            if running is None or running.token != answered_turn_token:
                # The turn ended while the answer was on its way, so the ask died with it.
                return False
            if not reached_the_backend:
                # The backend never got it, so the ask is still waiting to be answered.
                running.pending_permission_ask_ids.add(ask_id)
                return False
            await self._append_event(
                state, PermissionAnsweredEventPayload(ask_id=ask_id, option_id=option_id)
            )
            return True

    async def answer_user_input(
        self,
        conversation_id: str,
        request_id: str,
        answers: tuple[UserInputAnswer, ...],
    ) -> bool:
        """Deliver one complete answer map and record it only after the backend takes it."""
        state = await self._conversation_state(conversation_id)
        if state is None:
            return False
        state.last_touched_monotonic = self._monotonic_now()

        async with state.lock:
            running = state.running_turn
            child = state.child
            if running is None or child is None:
                return False
            questions = running.pending_user_input_questions.get(request_id)
            if questions is None:
                return False
            question_ids = tuple(question.question_id for question in questions)
            answers_by_question_id = {answer.question_id: answer for answer in answers}
            if len(answers_by_question_id) != len(answers) or set(
                answers_by_question_id
            ) != set(question_ids):
                return False
            ordered_answers = tuple(
                answers_by_question_id[question_id] for question_id in question_ids
            )
            if any(
                not answer.answers
                or any(not value for value in answer.answers)
                or (not question.multi_select and len(answer.answers) != 1)
                or (
                    not question.allow_other
                    and any(
                        value not in {option.label for option in question.options}
                        for value in answer.answers
                    )
                )
                for question, answer in zip(questions, ordered_answers, strict=True)
            ):
                return False
            del running.pending_user_input_questions[request_id]
            answered_turn_token = running.token

        reached_the_backend = True
        try:
            await child.answer_user_input(request_id, ordered_answers)
        except UserInputAnswerWriteFailed:
            reached_the_backend = False

        async with state.lock:
            running = state.running_turn
            if running is None or running.token != answered_turn_token:
                return False
            if not reached_the_backend:
                running.pending_user_input_questions[request_id] = questions
                return False
            await self._append_event(
                state,
                UserInputAnsweredEventPayload(
                    request_id=request_id, answers=ordered_answers
                ),
            )
            return True

    # --- running the system -------------------------------------------------------------

    async def start_idle_child_janitor(self) -> None:
        """Begin stopping children that have sat idle long enough to have been forgotten."""
        if self._idle_child_janitor is None:
            self._idle_child_janitor = asyncio.create_task(self._sweep_idle_children_forever())

    async def shutdown(self) -> None:
        """Stop the janitor, stop taking backend news, and stop every child."""
        janitor = self._idle_child_janitor
        self._idle_child_janitor = None
        if janitor is not None:
            janitor.cancel()
            with suppress(asyncio.CancelledError):
                await janitor
        async with self._conversations_lock:
            states = list(self._conversations.values())
        for state in states:
            pump = state.backend_event_pump
            state.backend_event_pump = None
            if pump is not None:
                pump.cancel()
                with suppress(asyncio.CancelledError):
                    await pump
            child = state.child
            state.child = None
            if child is not None:
                await self._stop_child(state, child)

    async def wait_until_quiescent(self) -> None:
        """Return once everything the system was going to do on its own has happened.

        Backend news is worked through on a queue per conversation, and a turn ending on
        that queue can set off a whole drain of held messages. This waits for all of it,
        which is what lets a test assert consequences instead of sleeping for them.
        """
        while True:
            async with self._conversations_lock:
                states = list(self._conversations.values())
            for state in states:
                await state.backend_event_queue.join()
            await asyncio.sleep(0)
            if all(state.backend_event_queue.empty() for state in states):
                return

    # --- the three modes ----------------------------------------------------------------

    async def _run_when_free(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
    ) -> PromptDeliveryFate:
        async with state.lock:
            # Held if anything at all is going on, and held if anything is already
            # waiting: a message that arrived later never runs earlier.
            if state.phase is not _ConversationPhase.idle or state.held_prompts:
                state.held_prompts.append(
                    _HeldPrompt(
                        held_prompt_id=f"held_{uuid4().hex}",
                        content=content,
                        sender_label=sender_label,
                        model_change=model_change,
                        reasoning_effort_change=reasoning_effort_change,
                        sender_message_id=sender_message_id,
                        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                        snapshot_sent_at_unix_milliseconds=(
                            sent_at_unix_milliseconds
                            if sent_at_unix_milliseconds is not None
                            else int(time.time() * 1000)
                        ),
                    )
                )
                self._publish_held_prompts_changed(state)
                return PromptDeliveryQueued(queue_position=len(state.held_prompts))
            reservation = self._reserve_turn(state)

        return await self._deliver_and_finalize(
            state,
            reservation,
            content=content,
            sender_label=sender_label,
            mode=PromptDeliveryMode.run_when_free,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
        )

    async def _send_now(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
    ) -> PromptDeliveryFate:
        await self._acquire_settled(state)
        try:
            running = state.running_turn
            if running is not None:
                try:
                    await self._cancel_and_end_running_turn(state, running)
                except BaseException:
                    # The incumbent's ending fell over, so this send never happens. The
                    # conversation goes back to whichever phase is now true rather than
                    # staying in an ending nobody is finishing.
                    self._settle_phase(state)
                    raise
            reservation = self._reserve_turn(state)
        finally:
            state.lock.release()

        return await self._deliver_and_finalize(
            state,
            reservation,
            content=content,
            sender_label=sender_label,
            mode=PromptDeliveryMode.send_now,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
            sender_message_id=sender_message_id,
            sent_at_unix_milliseconds=sent_at_unix_milliseconds,
        )

    async def _steer(
        self,
        state: _ConversationState,
        content: MessageContent,
        sender_label: str,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
    ) -> PromptDeliveryFate:
        # Whether a backend can steer is a fact about the backend, settled before any
        # child is touched: a steer at codex or claude spawns nothing.
        if not backend_supports_steer(state.record.backend_key):
            return PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.backend_cannot_steer
            )

        await self._acquire_settled(state)
        try:
            running = state.running_turn
            child = state.child
            if running is None or child is None:
                return PromptDeliveryRefused(
                    refusal_reason=PromptDeliveryRefusalReason.no_running_turn_to_steer_into
                )
        finally:
            state.lock.release()

        try:
            await child.steer(content, sender_label=sender_label)
        except PromptWriteFailed:
            return PromptDeliveryRefused(
                refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
            )

        async with state.lock:
            # The message entered the wire of the turn that was running, so it is
            # recorded even in the rare case where that turn ended while it was on its way.
            await self._append_event(
                state,
                PromptEventPayload(
                    content=content,
                    sender_label=sender_label,
                    mode=PromptDeliveryMode.steer,
                    sender_message_id=sender_message_id,
                    sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                ),
            )
        return PromptDeliveryInjected()

    # --- delivering ---------------------------------------------------------------------

    async def _deliver_and_finalize(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        *,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
    ) -> PromptDeliveryFate:
        try:
            delivery = await self._deliver_prompt(
                state,
                reservation.token,
                content=content,
                sender_label=sender_label,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
            )
            started = await self._finalize_delivery(
                state,
                reservation,
                delivery.refusal_reason,
                content=content,
                sender_label=sender_label,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
                sender_message_id=sender_message_id,
                sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                record_refusal=delivery.record_refusal,
                phase_when_not_started=_ConversationPhase.idle,
            )
        except BaseException:
            self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
            raise
        if started:
            return PromptDeliveryStarted()
        refusal = delivery.refusal_reason
        assert refusal is not None
        # The agent is free and this message is not going anywhere, so whatever was waiting
        # for it is owed its run. A send-now has already killed the incumbent to get here.
        await self._drain_held_prompts(state)
        return PromptDeliveryRefused(refusal_reason=refusal)

    async def _deliver_prompt(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        *,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
    ) -> _PromptDeliveryAttempt:
        """Get the message onto a live child's wire, or name why that was impossible.

        No lock is held here: spawning a process and loading a session take as long as they
        take, and the conversation stays readable while they do.
        """
        try:
            child = await self._ensure_child(state)
        except BackendSpawnFailed:
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.backend_did_not_start)
        except SessionLoadFailed:
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.session_did_not_load)

        composed = await self._compose_prompt_content(state, content)
        try:
            await child.write_prompt(
                turn_token,
                composed,
                sender_label=sender_label,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
            )
        except PromptWriteFailed:
            return _PromptDeliveryAttempt(PromptDeliveryRefusalReason.write_to_backend_failed)
        except NeedsRebind as rebind:
            refusal = await self._rebind_and_write_prompt(
                state,
                turn_token,
                content=composed,
                sender_label=sender_label,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
            )
            return _PromptDeliveryAttempt(
                refusal,
                record_refusal=refusal is not None and rebind.failed_child_recovery,
            )
        return _PromptDeliveryAttempt()

    async def _rebind_and_write_prompt(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        *,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
    ) -> PromptDeliveryRefusalReason | None:
        """Start the child again on the new values, under the same conversation, and write.

        This is the path for a backend that cannot be moved onto another model without
        starting over. The conversation is the same one — it resumes from the session
        cursor it already has — and if any of it fails, the change has not happened and
        neither has the delivery.

        A rebound child that is then not written to is thrown away rather than kept. It
        was started on the values the failed delivery was carrying, and those values did
        not stand, so keeping it would leave the conversation's record and its live agent
        saying two different things about what it is running on. Dropping it costs one
        lazy respawn and keeps the record the only answer.
        """
        old_child = state.child
        state.child = None
        if old_child is not None:
            await self._stop_child(state, old_child)
        try:
            child = await self._ensure_child(
                state, model=model_change, reasoning_effort=reasoning_effort_change
            )
        except BackendSpawnFailed:
            return PromptDeliveryRefusalReason.backend_did_not_start
        except SessionLoadFailed:
            return PromptDeliveryRefusalReason.session_did_not_load
        try:
            await child.write_prompt(
                turn_token,
                content,
                sender_label=sender_label,
                mode=mode,
                model_change=model_change,
                reasoning_effort_change=reasoning_effort_change,
            )
        except (PromptWriteFailed, NeedsRebind):
            await self._discard_child(state, child)
            return PromptDeliveryRefusalReason.write_to_backend_failed
        except BaseException:
            await self._discard_child(state, child)
            raise
        return None

    async def _finalize_delivery(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        refusal: PromptDeliveryRefusalReason | None,
        *,
        content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        sender_message_id: str | None,
        sent_at_unix_milliseconds: int | None,
        record_refusal: bool,
        phase_when_not_started: _ConversationPhase,
    ) -> bool:
        """Turn a delivery that has happened, or failed to, into the record and the state.

        The rows are written before the turn is admitted, so nothing the backend says about
        this turn can be recorded ahead of the prompt that started it.
        """
        async with state.lock:
            try:
                if refusal is not None:
                    if record_refusal:
                        await self._append_event(
                            state,
                            PromptDeliveryRefusedEventPayload(
                                content=content,
                                sender_label=sender_label,
                                mode=mode,
                                refusal_reason=refusal,
                                sender_message_id=sender_message_id,
                            ),
                        )
                    self._set_phase(state, phase_when_not_started)
                    return False

                carried_change: ModelChangedEventPayload | None = None
                if model_change is not None or reasoning_effort_change is not None:
                    running_on = state.record.model if model_change is None else model_change
                    if running_on is None:
                        # A change that does not name a model moves the one the record
                        # holds, and this row holds none. The same rows cannot start a
                        # child either, so this says what that says: nothing to run on.
                        raise ConversationRecordNamesNoModel(state.record.conversation_id)
                    carried_change = ModelChangedEventPayload(
                        model=running_on,
                        reasoning_effort=(
                            state.record.reasoning_effort
                            if reasoning_effort_change is None
                            else reasoning_effort_change
                        ),
                    )

                # One transaction: the change, the conversation's new values and the
                # prompt are one fact about one delivery, and a half-written one would
                # leave the record saying something that never happened.
                written = await self._store.append_delivered_prompt(
                    state.record.conversation_id,
                    prompt=PromptEventPayload(
                        content=content,
                        sender_label=sender_label,
                        mode=mode,
                        sender_message_id=sender_message_id,
                        sent_at_unix_milliseconds=sent_at_unix_milliseconds,
                    ),
                    model_change=carried_change,
                )
                self._take_in_written_rows(state, written)
                if carried_change is not None:
                    state.record = replace(
                        state.record,
                        model=carried_change.model,
                        reasoning_effort=carried_change.reasoning_effort,
                    )
                state.has_delivered_prompt = True
                state.running_turn = _RunningTurn(token=reservation.token)
                self._set_phase(state, _ConversationPhase.running)
                return True
            finally:
                state.reserved_turn = None
                if state.phase is _ConversationPhase.starting:
                    # Nothing above reached a resting phase, so writing the record fell
                    # over part-way. The conversation is handed back rather than left
                    # looking like it is still starting a turn that will never start.
                    self._set_phase(state, phase_when_not_started)
                reservation.resolved.set()

    async def _drain_held_prompts(self, state: _ConversationState) -> None:
        """Run held messages until one starts a turn or there are none left.

        One owner: the conversation is in its draining phase for the whole of it, so a
        second caller finding it idle a moment later does not start draining beside this
        one. A held message that cannot be delivered has its own refusal recorded — the
        caller that sent it is long gone — and the next one is tried.
        """
        async with state.lock:
            if state.phase is not _ConversationPhase.idle or not state.held_prompts:
                return
            self._set_phase(state, _ConversationPhase.draining)

        while True:
            async with state.lock:
                if not state.held_prompts:
                    self._set_phase(state, _ConversationPhase.idle)
                    return
                held = state.held_prompts.popleft()
                self._publish_held_prompts_changed(state)
                reservation = self._reserve_turn(state)

            try:
                delivery = await self._deliver_prompt(
                    state,
                    reservation.token,
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=PromptDeliveryMode.run_when_free,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                )
                started = await self._finalize_delivery(
                    state,
                    reservation,
                    delivery.refusal_reason,
                    content=held.content,
                    sender_label=held.sender_label,
                    mode=PromptDeliveryMode.run_when_free,
                    model_change=held.model_change,
                    reasoning_effort_change=held.reasoning_effort_change,
                    sender_message_id=held.sender_message_id,
                    sent_at_unix_milliseconds=held.sent_at_unix_milliseconds,
                    record_refusal=True,
                    phase_when_not_started=_ConversationPhase.draining,
                )
            except BaseException:
                # The drain is over, so the conversation goes back to nobody owning it
                # rather than staying in a drain that has stopped happening.
                self._abandon_reserved_turn(state, reservation, _ConversationPhase.idle)
                raise
            if started:
                return

    async def _discard_held_prompts(self, state: _ConversationState) -> None:
        """Throw away everything waiting, writing each one down. The lock must be held.

        Each message leaves the queue before its row is written, so a write that falls
        over part-way leaves the rest of the queue discarded rather than delivered.
        """
        while state.held_prompts:
            discarded = state.held_prompts.popleft()
            self._publish_held_prompts_changed(state)
            await self._append_event(
                state,
                PromptDiscardedEventPayload(
                    content=discarded.content,
                    sender_label=discarded.sender_label,
                    sender_message_id=discarded.sender_message_id,
                ),
            )

    async def _record_promoted_refusal(
        self,
        state: _ConversationState,
        held: _HeldPrompt,
        mode: PromptDeliveryMode,
        refusal: PromptDeliveryRefusalReason,
    ) -> None:
        """Keep the fate of a claimed held message after its original caller left."""
        await self._append_event(
            state,
            PromptDeliveryRefusedEventPayload(
                content=held.content,
                sender_label=held.sender_label,
                mode=mode,
                refusal_reason=refusal,
                sender_message_id=held.sender_message_id,
            ),
        )

    # --- turns --------------------------------------------------------------------------

    def _reserve_turn(self, state: _ConversationState) -> _ReservedTurn:
        """Name the turn that is about to be written. The lock must be held."""
        reservation = _ReservedTurn(
            token=TurnToken(
                conversation_id=state.record.conversation_id,
                turn_number=state.next_turn_number,
            ),
            resolved=asyncio.Event(),
        )
        # A new turn may say it is thinking straight away. Holding its first pulse back
        # because the turn before it had just said so would silence the very moment this
        # exists for: a turn that has started and has nothing to show yet.
        state.model_thinking_shown_at_monotonic = 0.0
        state.next_turn_number += 1
        state.reserved_turn = reservation
        self._set_phase(state, _ConversationPhase.starting)
        return reservation

    def _abandon_reserved_turn(
        self,
        state: _ConversationState,
        reservation: _ReservedTurn,
        phase: _ConversationPhase,
    ) -> None:
        """Give up a turn that was named and will now never start.

        No lock: whoever reserved the turn owns it, and owns the conversation's phase,
        until it resolves — so there is nothing here to serialise against. It matters most
        when something has gone wrong in an adapter, because a reservation that is never
        resolved is a conversation nothing can use again.
        """
        if state.reserved_turn is reservation:
            state.reserved_turn = None
        self._set_phase(state, phase)
        reservation.resolved.set()

    async def _cancel_and_end_running_turn(
        self, state: _ConversationState, running: _RunningTurn
    ) -> None:
        """Stop the agent and record the interruption. The lock is held on both sides.

        It is let go for the cancel itself, which is a wire call; the conversation is in
        its ending phase throughout, so nobody else takes it in the meantime. The backend
        will report this turn's ending as soon as it takes the cancel, and that report
        arrives while the lock is let go — the turn is marked as being ended here first, so
        the report is dropped and this interruption is the one that gets recorded.
        """
        self._set_phase(state, _ConversationPhase.ending)
        running.ending_is_the_cores = True
        child = state.child
        state.lock.release()
        try:
            if child is not None:
                await self._cancel_child_turn(state, child)
        finally:
            await state.lock.acquire()
        await self._end_turn(state, running, ConversationTurnEnding.interrupted, None)

    async def _end_turn(
        self,
        state: _ConversationState,
        running: _RunningTurn,
        ending: ConversationTurnEnding,
        error_summary: str | None,
    ) -> StoredConversationEvent | None:
        """Record a turn's ending, once. The lock must be held.

        Once per turn, whoever gets there first: a turn that has already ended writes
        nothing and returns nothing. A turn has one ending, and two rows saying it stopped
        would be two different stories about the same moment.

        The turn's permission asks die here: nothing more can be answered on them, and no
        answer is recorded for them. Settling them with the backend belongs to the adapter,
        which is the only side that knows what a withdrawn ask means to its vendor.
        """
        if running.ended:
            return None
        running.ended = True
        running.pending_permission_ask_ids.clear()
        running.pending_user_input_questions.clear()
        state.last_ended_turn_token = running.token
        state.running_turn = None
        return await self._append_event(
            state, TurnEndedEventPayload(ending=ending, error_summary=error_summary)
        )

    # --- the child ----------------------------------------------------------------------

    async def _ensure_child(
        self,
        state: _ConversationState,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> BackendChild:
        """The conversation's live child, spawned and bound on first need.

        ``model`` and ``reasoning_effort`` name values a delivery is carrying, so a child
        that has to be spawned for that delivery is spawned on them rather than on the
        values it is replacing.
        """
        child = state.child
        if child is not None:
            return child

        resolved_start = state.record.resolved_start()
        if model is not None or reasoning_effort is not None:
            resolved_start = replace(
                resolved_start,
                model=resolved_start.model if model is None else model,
                reasoning_effort=(
                    resolved_start.reasoning_effort
                    if reasoning_effort is None
                    else reasoning_effort
                ),
            )
        factory = self._backend_child_factories[state.record.backend_key]
        child = factory(
            resolved_start=resolved_start,
            event_sink=_CoreBackendEventSink(self, state),
            message_files=self._message_files,
        )
        # The pump is running before the child is, so news the child makes while it starts
        # up — the session cursor it mints — has somewhere to go.
        self._ensure_backend_event_pump(state)
        if self._backend_lifecycle is None:
            await child.start(
                resolved_start, vendor_session_cursor=state.record.vendor_session_cursor
            )
        else:
            async with self._backend_lifecycle.child_start(state.record.backend_key):
                await child.start(
                    resolved_start, vendor_session_cursor=state.record.vendor_session_cursor
                )
        state.child = child
        return child

    async def _discard_child(self, state: _ConversationState, child: BackendChild) -> None:
        """Stop a child and forget it, so the next message starts a fresh one.

        The new one resumes from the stored session cursor on the values the record
        holds, which is how a conversation gets back to one answer about itself.
        """
        if state.child is child:
            state.child = None
        await self._stop_child(state, child)

    async def _stop_child(self, state: _ConversationState, child: BackendChild) -> None:
        try:
            await child.stop()
        except Exception:
            LOGGER.exception(
                "conversation %s could not stop its backend child cleanly",
                state.record.conversation_id,
            )
            # The process may still be alive. Keep it counted so installation maintenance
            # remains conservatively refused rather than mutating underneath it.
            return
        if self._backend_lifecycle is not None:
            await self._backend_lifecycle.child_stopped(state.record.backend_key)

    async def _cancel_child_turn(self, state: _ConversationState, child: BackendChild) -> None:
        """Tell the child to stop its turn, and deal honestly with a cancel that failed.

        The ending is the core's and it stands either way: an agent that would not take
        the cancel does not get to keep the turn open in the record. But a cancel that did
        not reach the child means its wire did not carry it, and a child that cannot be
        told to stop cannot be trusted to be told anything — it may still be working on a
        turn this conversation considers over. So it is stopped and thrown away, and the
        next message spawns a fresh one from the session cursor. The failure gets a line of
        its own, because "the agent was interrupted" and "the agent was told to stop" have
        just stopped being the same statement.
        """
        try:
            await child.cancel_running_turn()
            return
        except Exception as cancel_failure:
            LOGGER.error(
                "conversation turn cancel failed conversation_id=%s backend=%s error=%r",
                state.record.conversation_id,
                str(state.record.backend_key),
                cancel_failure,
            )
        await self._discard_child(state, child)

    async def _compose_prompt_content(
        self, state: _ConversationState, content: MessageContent
    ) -> MessageContent:
        """The message as the backend gets it: the role text rides the very first prompt.

        The role is composed here, by the core, so every backend is told what it is in the
        same way. It goes on once, on the first prompt this conversation ever delivers —
        after that the agent has it. The record keeps the sender's own message: the role is
        the conversation's, and it is stored on the conversation.

        A message that opens with words takes the role onto those words, which is what
        joining two strings always did and leaves an ordinary prompt exactly the text it
        used to be. A message that opens with a picture takes the role as a piece in front
        of it, because there is no run of words at the front to join it to.
        """
        role_text = state.record.role_text
        if role_text is None or state.has_delivered_prompt:
            return content
        if await self._store.has_delivered_prompt(state.record.conversation_id):
            state.has_delivered_prompt = True
            return content
        return prefix_message_content_text(content, role_text, ROLE_TEXT_PROMPT_SEPARATOR)

    # --- backend news -------------------------------------------------------------------

    def _ensure_backend_event_pump(self, state: _ConversationState) -> None:
        pump = state.backend_event_pump
        if pump is None or pump.done():
            state.backend_event_pump = asyncio.create_task(self._pump_backend_events(state))

    async def _pump_backend_events(self, state: _ConversationState) -> None:
        """Work through one conversation's backend news, one item at a time, in order."""
        queue = state.backend_event_queue
        while True:
            handle = await queue.get()
            try:
                await handle()
            except Exception:
                LOGGER.exception(
                    "conversation %s could not take in a backend event",
                    state.record.conversation_id,
                )
            finally:
                queue.task_done()

    async def _hold_for_the_live_turn(
        self, state: _ConversationState, turn_token: TurnToken
    ) -> _RunningTurn | None:
        """Wait until this token's fate is settled, then take the lock if it is the live turn.

        Returns with the conversation's lock held when the news belongs to the turn running
        now, and ``None`` — lock released — when it belongs to a turn that is over, or one
        that never started.
        """
        while True:
            reserved = state.reserved_turn
            if reserved is None or reserved.token != turn_token:
                break
            await reserved.resolved.wait()

        await state.lock.acquire()
        running = state.running_turn
        if running is None or running.token != turn_token:
            state.lock.release()
            return None
        state.last_touched_monotonic = self._monotonic_now()
        return running

    async def _hold_for_the_live_or_most_recent_ended_turn(
        self, state: _ConversationState, turn_token: TurnToken
    ) -> bool:
        """Take the lock when a finished message still belongs to this conversation.

        Most backend news is meaningful only while its turn is live. A completed agent
        message is different: it is durable conversation content, and some persistent
        backends report a turn ending before delivering the parent's follow-up message.
        The most recently ended token remains valid for that one kind of news. Keeping the
        allowance to one token prevents arbitrarily old child output from reappearing.
        """
        while True:
            reserved = state.reserved_turn
            if reserved is None or reserved.token != turn_token:
                break
            await reserved.resolved.wait()

        await state.lock.acquire()
        running = state.running_turn
        if not (
            (running is not None and running.token == turn_token)
            or state.last_ended_turn_token == turn_token
        ):
            state.lock.release()
            return False
        state.last_touched_monotonic = self._monotonic_now()
        return True

    async def _on_token_usage_reported(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        payload: TokenUsageEventPayload,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_event(state, payload)
        finally:
            state.lock.release()

    async def _on_context_compacted(
        self, state: _ConversationState, turn_token: TurnToken
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_event(state, ContextCompactedEventPayload())
        finally:
            state.lock.release()

    async def _on_agent_message_completed(
        self, state: _ConversationState, turn_token: TurnToken, content: MessageContent
    ) -> None:
        if not await self._hold_for_the_live_or_most_recent_ended_turn(state, turn_token):
            return
        try:
            await self._append_event(state, AgentMessageEventPayload(content=content))
        finally:
            state.lock.release()

    async def _on_tool_call_started(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_event(
                state,
                ToolCallStartedEventPayload(
                    tool_call_id=tool_call_id, title=title, tool_kind=tool_kind, detail=detail
                ),
            )
        finally:
            state.lock.release()

    async def _on_tool_call_finished(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_event(
                state,
                ToolCallFinishedEventPayload(
                    tool_call_id=tool_call_id, tool_call_status=tool_call_status, detail=detail
                ),
            )
        finally:
            state.lock.release()

    async def _on_plan_updated(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        entries: tuple[PlanEntry, ...],
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_event(state, PlanUpdatedEventPayload(entries=entries))
        finally:
            state.lock.release()

    async def _on_permission_ask_raised(
        self, state: _ConversationState, turn_token: TurnToken, ask: BackendPermissionAsk
    ) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        try:
            await self._append_event(
                state,
                PermissionAskedEventPayload(
                    ask_id=ask.ask_id, title=ask.title, detail=ask.detail, options=ask.options
                ),
            )
            running.pending_permission_ask_ids.add(ask.ask_id)
        finally:
            state.lock.release()

    async def _on_user_input_requested(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        request: BackendUserInputRequest,
    ) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        try:
            await self._append_event(
                state,
                UserInputRequestedEventPayload(
                    request_id=request.request_id, questions=request.questions
                ),
            )
            running.pending_user_input_questions[request.request_id] = request.questions
        finally:
            state.lock.release()

    async def _on_user_input_failed(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        request_id: str,
        detail: str,
    ) -> None:
        if await self._hold_for_the_live_turn(state, turn_token) is None:
            return
        try:
            await self._append_event(
                state, UserInputFailedEventPayload(request_id=request_id, detail=detail)
            )
        finally:
            state.lock.release()

    async def _on_turn_ended(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        running = await self._hold_for_the_live_turn(state, turn_token)
        if running is None:
            return
        if running.ending_is_the_cores:
            # The core asked for this ending and is part-way through recording it as the
            # interruption it was. The backend answering "that turn has stopped" is the
            # cancel landing, not a second thing that happened.
            state.lock.release()
            return
        recorded_at_sequence: int | None = None
        try:
            try:
                stored = await self._end_turn(state, running, ending, error_summary)
                recorded_at_sequence = None if stored is None else stored.sequence
            except Exception as ending_not_written:
                # Both of the ways a person finds out a turn ended are the record and the
                # log. Losing the row must not cost the line as well — that would be a
                # turn that ended and left no trace anywhere.
                LOGGER.error(
                    "conversation turn ending could not be recorded conversation_id=%s "
                    "backend=%s ending=%s error=%r",
                    state.record.conversation_id,
                    str(state.record.backend_key),
                    str(ending),
                    ending_not_written,
                )
            finally:
                # The turn is over whether or not its ending could be written down, and a
                # conversation whose turn is over is idle.
                self._settle_phase(state)
        finally:
            state.lock.release()
        if ending is ConversationTurnEnding.failed:
            # Not conditional on the row: a failed turn's line is the one an operator
            # reads, and it says plainly when there is no row to go and look at.
            self._log_failed_turn(
                state, recorded_at_sequence, error_summary, standard_error_tail
            )
        await self._drain_held_prompts(state)

    async def _on_vendor_session_cursor_rebound(
        self, state: _ConversationState, vendor_session_cursor: str
    ) -> None:
        await self._store.update_vendor_session_cursor(
            state.record.conversation_id, vendor_session_cursor
        )
        state.record = replace(state.record, vendor_session_cursor=vendor_session_cursor)

    async def _on_available_commands_reported(
        self, state: _ConversationState, available_commands: tuple[AgentCommand, ...]
    ) -> None:
        """The whole menu, as the backend has it now, put where the last one was.

        It goes onto the conversation and nowhere near its record: which commands an agent
        answers to is something that is true about it, not something that happened in the
        conversation, so there is nothing here for a transcript to show.
        """
        await self._store.replace_available_commands(
            state.record.conversation_id, available_commands
        )
        state.record = replace(state.record, available_commands=available_commands)

    def _log_failed_turn(
        self,
        state: _ConversationState,
        sequence: int | None,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        """One line for a turn that failed, with everything needed to go and look.

        The turn's failure is normally a row too; this is the conversation system's own
        record of it, in the place an operator reads. ``sequence`` says where to find that
        row, and is ``None`` when the ending could not be written — the failure still gets
        its line, saying there is nothing to look up. Both free-text pieces go in quoted,
        so a multi-line stderr tail stays one line.
        """
        LOGGER.error(
            "conversation turn failed conversation_id=%s backend=%s sequence=%s error=%r "
            "standard_error_tail=%r",
            state.record.conversation_id,
            str(state.record.backend_key),
            sequence,
            error_summary,
            _standard_error_tail(standard_error_tail),
        )

    # --- the idle janitor ---------------------------------------------------------------

    async def _sweep_idle_children_forever(self) -> None:
        while True:
            await asyncio.sleep(self._idle_child_sweep_interval_seconds)
            try:
                await self._sweep_idle_children()
            except Exception:
                LOGGER.exception("the idle conversation child sweep failed")

    async def _sweep_idle_children(self) -> None:
        """Stop children that have sat idle long enough, silently.

        A conversation whose lock is held is in the middle of something and is not idle, so
        it is left for the next sweep rather than waited on.

        Stopping happens under the conversation's lock, wire call and all — the same
        reasoning as a kill, and for the same reason: forgetting the child and stopping it
        are one act. Done separately, a message arriving in between spawns its own child
        while the one being stopped is still alive, and the conversation briefly has two
        agents in it.
        """
        async with self._conversations_lock:
            states = list(self._conversations.values())
        for state in states:
            if state.child is None or state.lock.locked():
                continue
            async with state.lock:
                child = state.child
                if child is None or state.phase is not _ConversationPhase.idle:
                    continue
                idle_for = self._monotonic_now() - state.last_touched_monotonic
                if idle_for < self._idle_child_stop_after_seconds:
                    continue
                state.child = None
                await self._stop_child(state, child)

    # --- shared internals ---------------------------------------------------------------

    async def _conversation_state(self, conversation_id: str) -> _ConversationState | None:
        """This process's state for a conversation, read from its row the first time.

        A conversation started before this process was is picked up here with no child and
        nothing running, which is the honest answer after a restart: the record is all
        there, and the next message resumes the session.
        """
        async with self._conversations_lock:
            state = self._conversations.get(conversation_id)
            if state is not None:
                return state
            record = await self._store.read_conversation(conversation_id)
            if record is None:
                return None
            state = _ConversationState(record=record)
            self._conversations[conversation_id] = state
            return state

    async def _append_event(
        self, state: _ConversationState, payload: ConversationEventPayload
    ) -> StoredConversationEvent:
        stored = await self._store.append_event(state.record.conversation_id, payload)
        self._take_in_written_rows(state, (stored,))
        return stored

    def _take_in_written_rows(
        self, state: _ConversationState, written: tuple[StoredConversationEvent, ...]
    ) -> None:
        """Move the conversation on to what has just been written, and show it.

        Rows are shown only once they are committed, so a watcher never sees a row that is
        not in the record — which is what lets a reader replay and then carry straight on.
        """
        if not written:
            return
        state.record = replace(state.record, latest_sequence=written[-1].sequence)
        if self._live_tail is not None:
            for stored in written:
                self._live_tail.publish_event(stored)

    def _publish_live_tail_frame(
        self,
        state: _ConversationState,
        turn_token: TurnToken,
        frame: ConversationLiveTailFrame,
    ) -> None:
        """Show something that has not finished arriving. Never waits, never stores.

        A frame from a turn this conversation has moved on from is dropped rather than
        shown. Rows already answer for themselves — a stale one is refused by its token
        before it is written — and a frame that skipped that check would put a dead turn's
        half-finished text into the tail of the turn running now.

        The check takes no lock: it reads what the conversation is on at this instant,
        which is all a frame with no ordering obligations needs, and an adapter reporting
        its stream must never be made to wait behind anything.
        """
        if self._live_tail is None or not self._names_a_turn_to_show(state, turn_token):
            return
        self._live_tail.publish_frame(state.record.conversation_id, frame)

    def _publish_held_prompts_changed(self, state: _ConversationState) -> None:
        """Tell open readers that the process-owned queue snapshot changed."""
        if self._live_tail is None:
            return
        self._live_tail.publish_frame(
            state.record.conversation_id, HeldPromptsChangedFrame()
        )

    def _publish_model_thinking(
        self, state: _ConversationState, turn_token: TurnToken
    ) -> None:
        """Say the model is thinking, at most so often, and never say what it thought.

        The turn is checked before the rate is, so that a dead turn's reasoning cannot use
        up the moment a live turn was about to speak in. After that the first pulse goes
        out immediately — being prompt is the whole point of it — and the rest of the burst
        is dropped until the interval is up.
        """
        if self._live_tail is None or not self._names_a_turn_to_show(state, turn_token):
            return
        now = self._monotonic_now()
        if now - state.model_thinking_shown_at_monotonic < MODEL_THINKING_PULSE_INTERVAL_SECONDS:
            return
        state.model_thinking_shown_at_monotonic = now
        self._live_tail.publish_frame(state.record.conversation_id, ModelThinkingFrame())

    def _names_a_turn_to_show(
        self, state: _ConversationState, turn_token: TurnToken
    ) -> bool:
        """Whether this token is the turn a watcher should be seeing text from.

        The turn that is running, and also the one being started: between reserving a turn
        and its prompt row being written, the text is already on the wire and an agent that
        answers that fast is answering the newest turn, not a dead one. What is refused is
        a token this conversation has left behind.
        """
        running = state.running_turn
        if running is not None and running.token == turn_token:
            return True
        reserved = state.reserved_turn
        return reserved is not None and reserved.token == turn_token

    def _settle_phase(self, state: _ConversationState) -> None:
        """Put the conversation into whichever resting phase its facts say it is in.

        Used wherever an ending finishes, including when it finishes badly: a phase left
        part-way through is a conversation nothing can ever take again, so the phase is
        put back to what is actually true rather than to what was expected.
        """
        self._set_phase(
            state,
            _ConversationPhase.idle
            if state.running_turn is None
            else _ConversationPhase.running,
        )

    def _set_phase(self, state: _ConversationState, phase: _ConversationPhase) -> None:
        state.phase = phase
        if phase in (_ConversationPhase.idle, _ConversationPhase.running):
            state.phase_settled.set()
        else:
            state.phase_settled.clear()

    async def _acquire_settled(self, state: _ConversationState) -> None:
        """Take the lock at a moment when nobody is part-way through anything.

        Callers that kill or replace the running turn need the conversation to hold still:
        a send-now cannot kill an incumbent that is still being written, and an interrupt
        cannot stop a turn that has not started yet. Both wait for a resting phase.
        """
        while True:
            await state.lock.acquire()
            if state.phase in (_ConversationPhase.idle, _ConversationPhase.running):
                return
            state.lock.release()
            await state.phase_settled.wait()


class _CoreBackendEventSink:
    """What an adapter reports its backend's news to.

    Every call hands the news to the conversation's queue and returns, so an adapter's
    reading of its wire is never held up by what the core does about it. The queue is
    worked through in order, which is why an adapter may report freely and still trust that
    a tool call it reported before a message is recorded before it.
    """

    def __init__(self, system: SqliteProcessConversationSystem, state: _ConversationState) -> None:
        self._system = system
        self._state = state

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        """Shown on the live tail and never stored: a delta is not a row.

        Deltas exist to be shown while they arrive; the finished message is what is
        recorded. This one goes straight out to whoever is watching, ahead of the queue
        the rows go through, because it is not a row and has nothing to be ordered
        against — the message it belongs to is written whole when it finishes. It still
        names its turn, and a turn the conversation has moved on from is not shown.
        """
        self._system._publish_live_tail_frame(
            self._state, turn_token, AgentMessageDeltaFrame(text_delta=text_delta)
        )

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        """Shown on the live tail and never stored: that it happened, and nothing more.

        It carries no content and there is nothing for it to be ordered against, so it
        goes straight out rather than through the queue the rows go through — and it is
        dropped, like any frame, if it names a turn this conversation has moved on from.
        """
        self._system._publish_model_thinking(self._state, turn_token)

    async def agent_message_completed(
        self, turn_token: TurnToken, content: MessageContent
    ) -> None:
        self._enqueue(
            partial(self._system._on_agent_message_completed, self._state, turn_token, content)
        )

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_token_usage_reported,
                self._state,
                turn_token,
                TokenUsageEventPayload(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cached_input_tokens=cached_input_tokens,
                    cost_usd=cost_usd,
                ),
            )
        )

    async def context_compacted(self, turn_token: TurnToken) -> None:
        self._enqueue(
            partial(self._system._on_context_compacted, self._state, turn_token)
        )

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_tool_call_started,
                self._state,
                turn_token,
                tool_call_id,
                title,
                tool_kind,
                detail,
            )
        )

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        """Shown on the live tail and never stored: progress is not a row.

        It goes straight out to whoever is watching rather than through the queue the rows
        go through, for the same reason a message delta does: it is not a row, so there is
        nothing for it to be ordered against. The tool call's finish is what is recorded.
        A turn the conversation has moved on from is not shown.
        """
        self._system._publish_live_tail_frame(
            self._state,
            turn_token,
            ToolCallProgressFrame(tool_call_id=tool_call_id, detail=detail),
        )

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_tool_call_finished,
                self._state,
                turn_token,
                tool_call_id,
                tool_call_status,
                detail,
            )
        )

    async def plan_updated(
        self, turn_token: TurnToken, entries: tuple[PlanEntry, ...]
    ) -> None:
        """A row like any other: it goes through the queue, in order, and is kept.

        A plan is not a passing thing to show — somebody opening this conversation later
        still needs to see what the agent set out to do — so it is written down rather
        than shown and forgotten.
        """
        self._enqueue(
            partial(self._system._on_plan_updated, self._state, turn_token, entries)
        )

    async def permission_ask_raised(
        self, turn_token: TurnToken, ask: BackendPermissionAsk
    ) -> None:
        self._enqueue(
            partial(self._system._on_permission_ask_raised, self._state, turn_token, ask)
        )

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        self._enqueue(
            partial(self._system._on_user_input_requested, self._state, turn_token, request)
        )

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_user_input_failed,
                self._state,
                turn_token,
                request_id,
                detail,
            )
        )

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_turn_ended,
                self._state,
                turn_token,
                ending,
                error_summary,
                standard_error_tail,
            )
        )

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        self._enqueue(
            partial(
                self._system._on_vendor_session_cursor_rebound,
                self._state,
                vendor_session_cursor,
            )
        )

    async def available_commands_reported(
        self, available_commands: tuple[AgentCommand, ...]
    ) -> None:
        self._enqueue(
            partial(
                self._system._on_available_commands_reported,
                self._state,
                available_commands,
            )
        )

    def _enqueue(self, handle: _BackendEventHandler) -> None:
        self._state.backend_event_queue.put_nowait(handle)


def _standard_error_tail(standard_error: str | None) -> str | None:
    if standard_error is None:
        return None
    return standard_error[-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS:]
