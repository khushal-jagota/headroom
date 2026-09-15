"""The real conversation system, driven through a backend adapter that is not a process.

The fake here is a backend adapter, not a fake conversation system: every contract rule
under test — the held queue, the fates, steer gating, permission bookkeeping, which rows
get written — is the real system's. What the fake stands in for is a child process and its
wire, and it keeps the backend side's own account of what reached it, so a test asking
"did this text actually get there?" is answered by something other than the system's own
report of what it did.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

import planner.conversation.system as conversation_system
from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerOutcome,
    BackendSteerRefused,
    BackendSteerUncertain,
    BackendUserInputRequest,
    NeedsRebind,
    PermissionAnswerWriteFailed,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
    UserInputAnswerWriteFailed,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    ConversationTurnReference,
    HeldPromptPromotionMode,
    PromptDeliveryInjected,
    PromptDeliveryMode,
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
    PromptQueueReason,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AgentMessageEventPayload,
    AutomaticCompactionResult,
    ConversationEventKind,
    ConversationTurnEnding,
    ExplicitReplyMissingEventPayload,
    HeldPromptsChangedFrame,
    MessageToOwnerEventPayload,
    ModelThinkingFrame,
    PermissionAskOption,
    PromptDeliveryRefusedEventPayload,
    PromptDeliveryUncertainEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    TurnEndedEventPayload,
    UserInputAnswer,
    UserInputAnsweredEventPayload,
    UserInputOption,
    UserInputQuestion,
    UserInputRequestedEventPayload,
)
from planner.conversation.live_tail import (
    ConversationLiveTail,
    ConversationTailSubscription,
)
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore, StoredConversationEvent
from planner.conversation.system import (
    MODEL_THINKING_PULSE_INTERVAL_SECONDS,
    SqliteProcessConversationSystem,
)
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema

VENDOR_SESSION_CURSOR = "vendor-session-1"

# Enough goes round the event loop for a task that was only just created to have run and
# come to rest. Used where a test needs the system to be genuinely part-way through
# something rather than to have finished it.
_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP = 20


# --- the backend side --------------------------------------------------------------------


@dataclass
class _FakeBackendWrite:
    """One write that reached the backend side, and whether it joined a running turn.

    ``content`` is the whole message the adapter was handed. ``text`` is its words, which
    is what most of these exercises ask about: they are about how messages are ordered,
    held and refused, and a message that is only words says all of that.
    """

    content: MessageContent
    sender_content: MessageContent | None = None
    steered: bool = False

    @property
    def text(self) -> str:
        return message_content_text(self.content)


@dataclass
class _FakeBackend:
    """One conversation's backend side, kept across every child spawned for it.

    It outlives its children on purpose: a conversation whose child was stopped and started
    again is still the same agent with the same session, and a test asking what the backend
    has seen should not have its answer reset by a respawn.
    """

    conversation_id: str
    writes: list[_FakeBackendWrite] = field(default_factory=list)
    steer_tokens: list[TurnToken] = field(default_factory=list)
    permission_answers: dict[str, str] = field(default_factory=dict)
    user_input_answers: dict[str, tuple[UserInputAnswer, ...]] = field(default_factory=dict)
    cancellations: int = 0
    session_starts: int = 0
    stops: int = 0
    model: str | None = None
    reasoning_effort: str | None = None
    started_from_cursor: str | None = None
    live_turn_token: TurnToken | None = None
    steer_outcome: BackendSteerOutcome = field(default_factory=BackendSteerAccepted)
    permission_asks_raised: int = 0
    sink: BackendEventSink | None = None

    # Armed failures. Each keeps reproducing at its own boundary until it is disarmed.
    spawn_fails: bool = False
    session_load_fails: bool = False
    write_fails: bool = False
    write_failures_remaining: int = 0
    permission_answer_write_fails: bool = False
    user_input_answer_write_fails: bool = False
    needs_rebind_once: bool = False
    needs_failed_child_recovery_once: bool = False
    ends_the_turn_while_writing: bool = False
    writes_raise_something_unnamed: bool = False
    stop_raises_something_unnamed: bool = False

    cancels_raise_something_unnamed: bool = False
    reports_its_ending_during_a_cancel: bool = False

    # How many children of this conversation are alive at once. A conversation has one
    # agent in it; anything else is two agents reading the same session.
    live_children: int = 0
    most_live_children_at_once: int = 0
    lifecycle_events: list[str] = field(default_factory=list)
    automatic_compaction_writes: list[bool] = field(default_factory=list)

    # Gates, for the tests that need the system to be genuinely part-way through
    # something while another caller arrives.
    writes_wait_for_release: asyncio.Event | None = None
    write_has_begun: asyncio.Event | None = None
    steers_wait_for_release: asyncio.Event | None = None
    steer_has_begun: asyncio.Event | None = None
    cancels_wait_for_release: asyncio.Event | None = None
    cancel_has_begun: asyncio.Event | None = None
    stops_wait_for_release: asyncio.Event | None = None
    stop_has_begun: asyncio.Event | None = None

    def written_texts(self) -> tuple[str, ...]:
        return tuple(write.text for write in self.writes)

    def sender_written_texts(self) -> tuple[str, ...]:
        return tuple(
            message_content_text(write.sender_content)
            for write in self.writes
            if write.sender_content is not None
        )


class _FakeBackendChild:
    """One child process stand-in. Everything it is told goes to its conversation's backend."""

    def __init__(self, backend: _FakeBackend, event_sink: BackendEventSink) -> None:
        self._backend = backend
        self._sink = event_sink

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        if self._backend.session_load_fails:
            raise SessionLoadFailed(self._backend.conversation_id)
        self._backend.session_starts += 1
        self._backend.live_children += 1
        self._backend.most_live_children_at_once = max(
            self._backend.most_live_children_at_once, self._backend.live_children
        )
        self._backend.started_from_cursor = vendor_session_cursor
        self._backend.model = resolved_start.model
        self._backend.reasoning_effort = resolved_start.reasoning_effort
        self._backend.sink = self._sink
        self._backend.lifecycle_events.append(
            "start:"
            f"{vendor_session_cursor}:{resolved_start.model}:"
            f"{resolved_start.reasoning_effort}"
        )
        if vendor_session_cursor is None:
            await self._sink.vendor_session_cursor_rebound(VENDOR_SESSION_CURSOR)

    async def write_prompt(
        self,
        turn_token: TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool = False,
    ) -> None:
        # The label and the mode travel with the text as metadata for backends that have a
        # channel for it. This stand-in has none, so it takes them and lets them go.
        del sender_label, mode
        if self._backend.needs_failed_child_recovery_once:
            self._backend.needs_failed_child_recovery_once = False
            raise NeedsRebind(self._backend.conversation_id, failed_child_recovery=True)
        if self._backend.needs_rebind_once:
            self._backend.needs_rebind_once = False
            raise NeedsRebind(self._backend.conversation_id)
        if self._backend.write_has_begun is not None:
            self._backend.write_has_begun.set()
        if self._backend.writes_wait_for_release is not None:
            await self._backend.writes_wait_for_release.wait()
        if self._backend.writes_raise_something_unnamed:
            raise RuntimeError("the adapter fell over")
        if self._backend.write_fails:
            raise PromptWriteFailed(self._backend.conversation_id)
        if self._backend.write_failures_remaining:
            self._backend.write_failures_remaining -= 1
            raise PromptWriteFailed(self._backend.conversation_id)
        if model_change is not None:
            self._backend.model = model_change
        if reasoning_effort_change is not None:
            self._backend.reasoning_effort = reasoning_effort_change
        self._backend.writes.append(
            _FakeBackendWrite(content=content, sender_content=sender_content)
        )
        self._backend.automatic_compaction_writes.append(automatic_compaction)
        self._backend.lifecycle_events.append(f"write:{message_content_text(content)}")
        self._backend.live_turn_token = turn_token
        if self._backend.ends_the_turn_while_writing:
            self._backend.ends_the_turn_while_writing = False
            await self._sink.turn_ended(
                turn_token,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            self._backend.live_turn_token = None
            # Hand the loop back enough times that the ending is certainly picked up while
            # this send is still on its way, which is the race the barrier is for.
            for _ in range(_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP):
                await asyncio.sleep(0)

    async def steer(
        self, turn_token: TurnToken, content: MessageContent, *, sender_label: str
    ) -> BackendSteerOutcome:
        del sender_label
        if self._backend.steer_has_begun is not None:
            self._backend.steer_has_begun.set()
        if self._backend.steers_wait_for_release is not None:
            await self._backend.steers_wait_for_release.wait()
        if self._backend.write_fails:
            raise PromptWriteFailed(self._backend.conversation_id)
        self._backend.steer_tokens.append(turn_token)
        self._backend.writes.append(_FakeBackendWrite(content=content, steered=True))
        return self._backend.steer_outcome

    async def cancel_running_turn(self) -> None:
        if self._backend.cancel_has_begun is not None:
            self._backend.cancel_has_begun.set()
        if self._backend.cancels_wait_for_release is not None:
            await self._backend.cancels_wait_for_release.wait()
        if self._backend.cancels_raise_something_unnamed:
            raise RuntimeError("the cancel never got to the child")
        self._backend.cancellations += 1
        token = self._backend.live_turn_token
        self._backend.live_turn_token = None
        if self._backend.reports_its_ending_during_a_cancel and token is not None:
            # What a real agent does: it takes the cancel and says its turn has stopped,
            # while the core is still part-way through recording the interruption.
            await self._sink.turn_ended(
                token,
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            )
            # A real cancel waits on its wire, which gives the queue time to work that
            # report through before the core comes back to record its own ending. Without
            # this the two never actually race and the report always loses by accident.
            for _ in range(_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP):
                await asyncio.sleep(0)

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        if self._backend.permission_answer_write_fails:
            raise PermissionAnswerWriteFailed(ask_id)
        self._backend.permission_answers[ask_id] = option_id

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        if self._backend.user_input_answer_write_fails:
            raise UserInputAnswerWriteFailed(request_id)
        self._backend.user_input_answers[request_id] = answers

    async def stop(self) -> None:
        if self._backend.stop_has_begun is not None:
            self._backend.stop_has_begun.set()
        if self._backend.stops_wait_for_release is not None:
            await self._backend.stops_wait_for_release.wait()
        if self._backend.stop_raises_something_unnamed:
            raise RuntimeError("the child did not stop")
        self._backend.stops += 1
        self._backend.lifecycle_events.append("stop")
        self._backend.live_children -= 1
        self._backend.live_turn_token = None


class _FakeMonotonicClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _Harness:
    """A real system over a real database, with every backend key on the fake adapter."""

    def __init__(
        self,
        db_path: Path,
        *,
        clock: _FakeMonotonicClock | None = None,
        idle_child_stop_after_seconds: float = 30 * 60,
        idle_child_sweep_interval_seconds: float = 5 * 60,
    ) -> None:
        self.clock = _FakeMonotonicClock() if clock is None else clock
        self.store = ConversationStore(str(db_path), integer_now=lambda: int(self.clock.now))
        self.backends: dict[str, _FakeBackend] = {}
        self.spawned_conversation_ids: list[str] = []
        self.live_tail = ConversationLiveTail()
        self.message_files = ConversationMessageFiles(str(db_path))
        self.system = SqliteProcessConversationSystem(
            store=self.store,
            message_files=self.message_files,
            backend_child_factories={
                backend_key: self._make_child for backend_key in ConversationBackendKey
            },
            live_tail=self.live_tail,
            monotonic_now=self.clock,
            unix_time_now=self.clock,
            idle_child_stop_after_seconds=idle_child_stop_after_seconds,
            idle_child_sweep_interval_seconds=idle_child_sweep_interval_seconds,
        )

    def _make_child(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _FakeBackendChild:
        backend = self.backend(resolved_start.conversation_id)
        self.spawned_conversation_ids.append(resolved_start.conversation_id)
        if backend.spawn_fails:
            raise BackendSpawnFailed(resolved_start.conversation_id)
        return _FakeBackendChild(backend, event_sink)

    def backend(self, conversation_id: str) -> _FakeBackend:
        return self.backends.setdefault(conversation_id, _FakeBackend(conversation_id))

    def watch(self, conversation_id: str) -> ConversationTailSubscription:
        """Watch a conversation the way a browser does, for what is shown and not kept."""
        return self.live_tail.subscribe(conversation_id)

    # --- driving the backend, each returning once the system has finished reacting ---

    async def settle(self) -> None:
        await self.system.wait_until_quiescent()

    async def _end_turn(
        self,
        conversation_id: str,
        ending: ConversationTurnEnding,
        error_summary: str | None = None,
        standard_error_tail: str | None = None,
    ) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        if token is None or backend.sink is None:
            return
        backend.live_turn_token = None
        await backend.sink.turn_ended(
            token,
            ending=ending,
            error_summary=error_summary,
            standard_error_tail=standard_error_tail,
        )
        await self.settle()

    async def complete_turn(self, conversation_id: str) -> None:
        await self._end_turn(conversation_id, ConversationTurnEnding.completed)

    async def fail_turn(
        self,
        conversation_id: str,
        error_summary: str = "the agent fell over",
        standard_error_tail: str | None = None,
    ) -> None:
        await self._end_turn(
            conversation_id, ConversationTurnEnding.failed, error_summary, standard_error_tail
        )

    async def raise_permission_ask(self, conversation_id: str) -> str:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        backend.permission_asks_raised += 1
        ask_id = f"ask-{backend.permission_asks_raised}"
        await backend.sink.permission_ask_raised(
            token,
            BackendPermissionAsk(
                ask_id=ask_id,
                title="Run a command?",
                detail="ls",
                options=(
                    PermissionAskOption(
                        option_id="allow-once", label="Approve once", option_kind="allow"
                    ),
                    PermissionAskOption(option_id="deny", label="Decline", option_kind="reject"),
                ),
            ),
        )
        await self.settle()
        return ask_id

    async def request_user_input(self, conversation_id: str) -> BackendUserInputRequest:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        request = BackendUserInputRequest(
            request_id="input-1",
            questions=(
                UserInputQuestion(
                    question_id="scope",
                    header="Scope",
                    question="Which parts?",
                    options=(
                        UserInputOption(label="Backend", description="Python"),
                        UserInputOption(label="Frontend", description="Svelte"),
                    ),
                    multi_select=True,
                    allow_other=True,
                ),
                UserInputQuestion(
                    question_id="timing",
                    header="Timing",
                    question="When?",
                    options=(UserInputOption(label="Now", description="Immediately"),),
                    multi_select=False,
                    allow_other=True,
                ),
            ),
        )
        await backend.sink.user_input_requested(token, request)
        await self.settle()
        return request

    async def agent_message(self, conversation_id: str, content: MessageContent) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.agent_message_completed(token, content)
        await self.settle()

    async def confirm_compaction(self, conversation_id: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.context_compacted(token)
        await self.settle()

    # --- reading the record ---

    async def events(self, conversation_id: str) -> tuple[StoredConversationEvent, ...]:
        return await self.store.read_events_after(conversation_id, 0)

    async def recorded_kinds(self, conversation_id: str) -> tuple[ConversationEventKind, ...]:
        return tuple(event.kind for event in await self.events(conversation_id))

    async def recorded_prompts(self, conversation_id: str) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            (
                message_content_text(event.payload.content),
                event.payload.sender_label,
                str(event.payload.mode),
            )
            for event in await self.events(conversation_id)
            if isinstance(event.payload, PromptEventPayload)
        )

    async def recorded_endings(self, conversation_id: str) -> tuple[ConversationTurnEnding, ...]:
        return tuple(
            event.payload.ending
            for event in await self.events(conversation_id)
            if isinstance(event.payload, TurnEndedEventPayload)
        )


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[_Harness]:
    db_path = tmp_path / "conversations.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    built = _Harness(db_path)
    yield built
    asyncio.run(built.system.shutdown())


def _run(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Every test drives the system from one event loop, as a real caller would.

    The time limit is a wedge detector: nothing here does real work, so anything near it
    is a system that has stopped rather than one that is slow.
    """
    asyncio.run(asyncio.wait_for(exercise(), 20.0))


async def _start(
    harness: _Harness,
    conversation_id: str = "c",
    *,
    backend_key: ConversationBackendKey = ConversationBackendKey.hermes,
    model: str = "a-model",
    reasoning_effort: str | None = None,
    role_materials: ConversationRoleMaterials | None = None,
) -> None:
    await harness.system.start_conversation(
        ConversationStartRequest(
            conversation_id=conversation_id,
            backend_key=backend_key,
            model=model,
            reasoning_effort=reasoning_effort,
            role_materials=role_materials,
            workspace_folder=Path("/tmp/workspace"),
        )
    )


# --- the conversation's record is written as step one ------------------------------------


def test_starting_a_conversation_writes_its_record_and_touches_nothing_else(
    harness: _Harness,
) -> None:
    """The README's first unobservable obligation, checked from the inside.

    Creation writes the row and does nothing else: no backend is spawned, and the
    conversation's record has the row it needs before anything could have been written
    into it.
    """
    written_calls: list[str] = []
    real_create = harness.store.create_conversation
    real_append = harness.store.append_event

    async def recording_create(resolved: ResolvedConversationStart):  # type: ignore[no-untyped-def]
        written_calls.append("create_conversation")
        return await real_create(resolved)

    async def recording_append(  # type: ignore[no-untyped-def]
        conversation_id: str, payload, **kwargs
    ):
        written_calls.append("append_event")
        return await real_append(conversation_id, payload, **kwargs)

    harness.store.create_conversation = recording_create  # type: ignore[method-assign]
    harness.store.append_event = recording_append  # type: ignore[method-assign]

    async def exercise() -> None:
        await _start(harness, "c")

        assert written_calls == ["create_conversation"]
        assert harness.spawned_conversation_ids == []
        assert harness.backend("c").session_starts == 0
        stored = await harness.store.read_conversation("c")
        assert stored is not None
        assert stored.latest_sequence == 0
        assert await harness.events("c") == ()
        assert await harness.system.is_running("c") is False

    _run(exercise)


def test_message_to_owner_is_one_ordered_idempotent_row(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        content = text_message_content("A reply for the owner")
        sender = Principal(PrincipalKind.ticket, "t_sender")
        recipient = Principal(PrincipalKind.owner, "owner")

        await harness.system.record_message_to_owner(
            "c",
            content,
            sender_label="Ticket t_sender",
            sender=sender,
            recipient=recipient,
            sender_message_id="owner-message-1",
            sent_at_unix_milliseconds=1234,
        )
        await harness.system.record_message_to_owner(
            "c",
            content,
            sender_label="Ticket t_sender",
            sender=sender,
            recipient=recipient,
            sender_message_id="owner-message-1",
            sent_at_unix_milliseconds=1234,
        )

        rows = await harness.events("c")
        assert len(rows) == 1
        payload = rows[0].payload
        assert isinstance(payload, MessageToOwnerEventPayload)
        assert payload.sender == sender
        assert payload.recipient == recipient
        assert harness.backends == {}

        with pytest.raises(ValueError, match="different message"):
            await harness.system.record_message_to_owner(
                "c",
                content,
                sender_label="Ticket t_sender",
                sender=Principal(PrincipalKind.ticket, "t_other"),
                recipient=recipient,
                sender_message_id="owner-message-1",
            )

    _run(exercise)


def test_backend_prose_is_runtime_only_and_silence_is_explicit(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        ticket = Principal(PrincipalKind.ticket, "t_worker")
        await harness.system.send(
            "c",
            text_message_content("Please report back"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=ticket,
        )
        await harness.agent_message("c", text_message_content("backend-only prose"))
        await harness.complete_turn("c")

        payloads = tuple(event.payload for event in await harness.events("c"))
        assert not any(isinstance(payload, AgentMessageEventPayload) for payload in payloads)
        assert [
            payload.prompt_sender
            for payload in payloads
            if isinstance(payload, ExplicitReplyMissingEventPayload)
        ] == [OWNER_PRINCIPAL]
        assert isinstance(payloads[-1], TurnEndedEventPayload)

    _run(exercise)


def test_explicit_owner_message_suppresses_silence_but_an_old_retry_does_not(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        ticket = Principal(PrincipalKind.ticket, "t_worker")
        content = text_message_content("Explicit result")

        async def prompt() -> None:
            await harness.system.send(
                "c",
                text_message_content("Please report back"),
                sender_label="owner",
                sender=OWNER_PRINCIPAL,
                recipient=ticket,
            )

        await prompt()
        await harness.system.record_message_to_owner(
            "c",
            content,
            sender_label="Ticket t_worker",
            sender=ticket,
            recipient=OWNER_PRINCIPAL,
            sender_message_id="reply-1",
        )
        await harness.complete_turn("c")
        assert not any(
            isinstance(event.payload, ExplicitReplyMissingEventPayload)
            for event in await harness.events("c")
        )

        await prompt()
        await harness.system.record_message_to_owner(
            "c",
            content,
            sender_label="Ticket t_worker",
            sender=ticket,
            recipient=OWNER_PRINCIPAL,
            sender_message_id="reply-1",
        )
        await harness.complete_turn("c")
        markers = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, ExplicitReplyMissingEventPayload)
        ]
        assert [marker.prompt_sender for marker in markers] == [OWNER_PRINCIPAL]

    _run(exercise)


def test_held_prompt_batch_marks_each_distinct_sender_in_first_seen_order(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        recipient = Principal(PrincipalKind.ticket, "t_worker")
        chief = Principal(PrincipalKind.chief, "chief")
        await harness.system.send("c", text_message_content("runtime"), sender_label="Panels")
        for text, sender in (("one", OWNER_PRINCIPAL), ("two", chief), ("three", OWNER_PRINCIPAL)):
            fate = await harness.system.send(
                "c",
                text_message_content(text),
                sender_label=sender.kind.value,
                sender=sender,
                recipient=recipient,
            )
            assert isinstance(fate, PromptDeliveryQueued)
        await harness.complete_turn("c")
        await harness.complete_turn("c")

        markers = [
            event.payload.prompt_sender
            for event in await harness.events("c")
            if isinstance(event.payload, ExplicitReplyMissingEventPayload)
        ]
        assert markers == [OWNER_PRINCIPAL, chief]

    _run(exercise)


def test_reply_credit_names_one_turn_and_the_end_lock_wins_the_race(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        ticket = Principal(PrincipalKind.ticket, "t_worker")

        async def begin() -> ConversationTurnReference:
            await harness.system.send(
                "c",
                text_message_content("Please report"),
                sender_label="owner",
                sender=OWNER_PRINCIPAL,
                recipient=ticket,
            )
            turn = await harness.system.active_turn_reference("c")
            assert turn is not None
            return turn

        ended_first = await begin()
        await harness.complete_turn("c")
        await harness.system.record_explicit_reply(ended_first, OWNER_PRINCIPAL)

        credited_first = await begin()
        await harness.system.record_explicit_reply(credited_first, OWNER_PRINCIPAL)
        await harness.complete_turn("c")

        markers = [
            event.payload.prompt_sender
            for event in await harness.events("c")
            if isinstance(event.payload, ExplicitReplyMissingEventPayload)
        ]
        assert markers == [OWNER_PRINCIPAL]

    _run(exercise)


def test_accepted_steer_adds_its_sender_to_the_active_turn(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        recipient = Principal(PrincipalKind.ticket, "t_worker")
        chief = Principal(PrincipalKind.chief, "chief")
        await harness.system.send(
            "c",
            text_message_content("Owner prompt"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
        )
        await harness.system.send(
            "c",
            text_message_content("Chief steer"),
            sender_label="Chief",
            mode=PromptDeliveryMode.steer,
            sender=chief,
            recipient=recipient,
        )
        turn = await harness.system.active_turn_reference("c")
        assert turn is not None
        await harness.system.record_explicit_reply(turn, OWNER_PRINCIPAL)
        await harness.complete_turn("c")

        markers = [
            event.payload.prompt_sender
            for event in await harness.events("c")
            if isinstance(event.payload, ExplicitReplyMissingEventPayload)
        ]
        assert markers == [chief]

    _run(exercise)


# --- a failing turn gets one error-log line ----------------------------------------------


def test_a_failed_turn_writes_one_error_log_line_carrying_where_to_look(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """The README's second unobservable obligation. One line, and enough of it to act on."""

    async def exercise() -> None:
        await _start(harness, "c", backend_key=ConversationBackendKey.codex)
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        with caplog.at_level(logging.ERROR, logger="planner.conversation"):
            await harness.fail_turn(
                "c",
                error_summary="the model refused",
                standard_error_tail="traceback line one\ntraceback line two",
            )

        records = [record for record in caplog.records if record.name == "planner.conversation"]
        assert len(records) == 1
        line = records[0].getMessage()
        assert "conversation_id=c" in line
        assert "backend=codex" in line
        assert "sequence=2" in line
        assert "the model refused" in line
        assert "traceback line two" in line
        # Quoted, so a stderr tail with newlines in it stays one line.
        assert "\n" not in line
        assert await harness.recorded_endings("c") == (ConversationTurnEnding.failed,)

    _run(exercise)


# --- the three modes against an idle and a busy agent ------------------------------------


def test_an_unconfirmed_steer_is_uncertain_and_leaves_the_turn_alone(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c", backend_key=ConversationBackendKey.hermes)
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        harness.backend("c").write_fails = True

        assert (
            await harness.system.send(
                "c",
                text_message_content("steered"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
            == PromptDeliveryUncertain()
        )
        assert harness.backend("c").written_texts() == ("incumbent",)
        assert await harness.system.is_running("c") is True
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.prompt_delivery_uncertain,
        )

    _run(exercise)


@pytest.mark.parametrize("replacement_starts", [False, True])
def test_an_accepted_steer_receipt_can_follow_its_turn_and_a_replacement(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    replacement_starts: bool,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        backend.steer_has_begun = asyncio.Event()
        backend.steers_wait_for_release = asyncio.Event()

        steering = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("steered"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
                sender_message_id="steer-id",
            )
        )
        await backend.steer_has_begun.wait()
        await harness.complete_turn("c")

        if replacement_starts:
            assert (
                await harness.system.send(
                    "c", text_message_content("replacement"), sender_label="owner"
                )
                == PromptDeliveryStarted()
            )

        backend.steers_wait_for_release.set()
        assert await steering == PromptDeliveryInjected()

        expected_prompts = [
            ("incumbent", "owner", "queue"),
            ("steered", "owner", "steer"),
        ]
        expected_kinds = [
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
            ConversationEventKind.prompt,
        ]
        if replacement_starts:
            expected_prompts.insert(1, ("replacement", "owner", "queue"))
            expected_kinds.insert(2, ConversationEventKind.prompt)
        assert list(await harness.recorded_prompts("c")) == expected_prompts
        assert list(await harness.recorded_kinds("c")) == expected_kinds
        assert await harness.system.is_running("c") is replacement_starts
        assert backend.steer_tokens == [TurnToken("c", 1)]

    _run(exercise)


@pytest.mark.parametrize(
    ("steer_outcome", "expected_fate", "recorded_payload_type"),
    [
        (BackendSteerAccepted(), PromptDeliveryInjected(), PromptEventPayload),
        (BackendSteerUncertain(), PromptDeliveryUncertain(), PromptDeliveryUncertainEventPayload),
    ],
)
def test_a_delayed_direct_steer_receipt_does_not_credit_its_sender_to_the_replacement_turn(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    steer_outcome: BackendSteerOutcome,
    expected_fate: PromptDeliveryInjected | PromptDeliveryUncertain,
    recorded_payload_type: type[PromptEventPayload] | type[PromptDeliveryUncertainEventPayload],
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        recipient = Principal(PrincipalKind.ticket, "t_worker")
        chief = Principal(PrincipalKind.chief, "chief")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        backend.steer_outcome = steer_outcome
        backend.steer_has_begun = asyncio.Event()
        backend.steers_wait_for_release = asyncio.Event()

        steering = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("late addressed steer"),
                sender_label="Chief",
                mode=PromptDeliveryMode.steer,
                sender_message_id="late-direct-id",
                sender=chief,
                recipient=recipient,
            )
        )
        await backend.steer_has_begun.wait()
        await harness.complete_turn("c")
        await harness.system.send(
            "c",
            text_message_content("replacement"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
        )
        replacement = await harness.system.active_turn_reference("c")
        assert replacement is not None
        await harness.system.record_explicit_reply(replacement, OWNER_PRINCIPAL)

        backend.steers_wait_for_release.set()
        assert await steering == expected_fate
        await harness.complete_turn("c")

        payloads = [event.payload for event in await harness.events("c")]
        assert any(
            isinstance(payload, recorded_payload_type)
            and payload.sender_message_id == "late-direct-id"
            and payload.sender == chief
            and payload.recipient == recipient
            for payload in payloads
        )
        assert not any(
            isinstance(payload, ExplicitReplyMissingEventPayload) for payload in payloads
        )
        assert backend.steer_tokens == [TurnToken("c", 1)]

    _run(exercise)


def test_a_provider_refused_steer_falls_back_to_one_queued_message(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        backend.steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.backend_rejected_steer
        )
        content = text_message_content("refused steer")

        first = await harness.system.send_with_receipt(
            "c",
            content,
            sender_label="owner",
            mode=PromptDeliveryMode.steer,
            sender_message_id="refused-id",
        )
        duplicate = await harness.system.send_with_receipt(
            "c",
            content,
            sender_label="owner",
            mode=PromptDeliveryMode.steer,
            sender_message_id="refused-id",
        )

        assert first.fate == PromptDeliveryQueued(queue_position=1)
        assert duplicate.fate == PromptDeliveryQueued(queue_position=1)
        assert first.newly_accepted
        assert not duplicate.newly_accepted
        assert backend.steer_tokens == [TurnToken("c", 1)]
        assert await harness.recorded_kinds("c") == (ConversationEventKind.prompt,)
        [held] = await harness.system.held_prompts("c")
        assert held.queue_reason is PromptQueueReason.steer_refused

    _run(exercise)


def test_a_refused_steer_starts_after_its_target_turn_ends(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        backend.steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.backend_rejected_steer
        )
        backend.steer_has_begun = asyncio.Event()
        backend.steers_wait_for_release = asyncio.Event()
        content = text_message_content("refused after ending")

        steering = asyncio.create_task(
            harness.system.send(
                "c",
                content,
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
                sender_message_id="late-refusal-id",
            )
        )
        await backend.steer_has_begun.wait()
        await harness.complete_turn("c")
        duplicate = asyncio.create_task(
            harness.system.send(
                "c",
                content,
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
                sender_message_id="late-refusal-id",
            )
        )
        await asyncio.sleep(0)
        assert duplicate.done() is False
        backend.steers_wait_for_release.set()

        assert await steering == PromptDeliveryStarted()
        assert await duplicate == PromptDeliveryStarted()
        assert await harness.recorded_prompts("c") == (
            ("incumbent", "owner", "queue"),
            ("refused after ending", "owner", "queue"),
        )
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
            ConversationEventKind.prompt,
        )
        assert await harness.system.held_prompts("c") == ()
        assert await harness.system.is_running("c") is True
        assert backend.steer_tokens == [TurnToken("c", 1)]

    _run(exercise)


def test_active_steer_with_attachment_or_run_change_queues_with_reason(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")

        attachment = await harness.system.send(
            "c",
            (MessageImage("image-1", "image/png"),),
            sender_label="owner",
            mode=PromptDeliveryMode.steer,
        )
        run_change = await harness.system.send(
            "c",
            text_message_content("change model"),
            sender_label="owner",
            mode=PromptDeliveryMode.steer,
            model_change="next-model",
        )

        assert attachment == PromptDeliveryQueued(queue_position=1)
        assert run_change == PromptDeliveryQueued(queue_position=2)
        held = await harness.system.held_prompts("c")
        assert [message.queue_reason for message in held] == [
            PromptQueueReason.attachment,
            PromptQueueReason.run_change,
        ]

    _run(exercise)


def test_an_uncertain_steer_survives_reload_without_retransmission(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        backend.steer_outcome = BackendSteerUncertain()
        content = text_message_content("uncertain steer")
        sender = Principal(PrincipalKind.ticket, "t_sender")
        recipient = Principal(PrincipalKind.chief, "chief")

        assert (
            await harness.system.send(
                "c",
                content,
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
                sender_message_id="uncertain-id",
                sender=sender,
                recipient=recipient,
            )
            == PromptDeliveryUncertain()
        )

        restarted = _Harness(tmp_path / "conversations.db")
        restarted.backends = harness.backends
        try:
            assert (
                await restarted.system.send(
                    "c",
                    content,
                    sender_label="owner",
                    mode=PromptDeliveryMode.steer,
                    sender_message_id="uncertain-id",
                    sender=sender,
                    recipient=recipient,
                )
                == PromptDeliveryUncertain()
            )
            with pytest.raises(ValueError, match="different message"):
                await restarted.system.send(
                    "c",
                    content,
                    sender_label="owner",
                    mode=PromptDeliveryMode.steer,
                    sender_message_id="uncertain-id",
                    sender=sender,
                    recipient=Principal(PrincipalKind.ticket, "t_other"),
                )
            assert backend.steer_tokens == [TurnToken("c", 1)]
            uncertain_rows = [
                event.payload
                for event in await restarted.events("c")
                if isinstance(event.payload, PromptDeliveryUncertainEventPayload)
            ]
            assert len(uncertain_rows) == 1
            assert uncertain_rows[0].sender_message_id == "uncertain-id"
        finally:
            await restarted.system.shutdown()

    _run(exercise)


# --- every refusal comes from its own cause, and reaches no backend ----------------------


# --- draining ----------------------------------------------------------------------------


# --- interrupting -------------------------------------------------------------------------


def test_a_turn_ends_once_and_a_dead_turns_ending_never_kills_its_successor(
    harness: _Harness,
) -> None:
    """The core killed the turn, so the adapter's late ending names a turn that is over.

    The message that was held has started a turn of its own by the time it arrives, and
    that turn is not the one the ending is about.
    """

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("held"), sender_label="owner")
        backend = harness.backend("c")
        stale_token = backend.live_turn_token
        assert stale_token is not None and backend.sink is not None

        await harness.system.interrupt("c")
        assert await harness.system.is_running("c") is True

        await backend.sink.turn_ended(
            stale_token,
            ending=ConversationTurnEnding.completed,
            error_summary=None,
            standard_error_tail=None,
        )
        await harness.settle()

        assert await harness.recorded_endings("c") == (ConversationTurnEnding.interrupted,)
        assert await harness.system.is_running("c") is True

    _run(exercise)


def test_an_agents_news_never_lands_before_the_prompt_that_asked_for_it(
    harness: _Harness,
) -> None:
    """An agent that answers before the send has finished writing its rows still queues."""

    async def exercise() -> None:
        await _start(harness, "c")
        # A turn first, so the child and its session are already there and the ending
        # below is the only thing the queue has to work through.
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.backend("c").ends_the_turn_while_writing = True

        fate = await harness.system.send(
            "c",
            text_message_content("quick one"),
            sender_label="owner",
        )
        await harness.settle()

        assert fate == PromptDeliveryStarted()
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
        )
        assert await harness.system.is_running("c") is False

    _run(exercise)


# --- a send can carry a model or reasoning-effort change ---------------------------------


def test_a_backend_that_can_only_change_by_starting_again_is_started_again(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c", backend_key=ConversationBackendKey.claude, model="start-model")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        backend = harness.backend("c")
        assert backend.session_starts == 1
        backend.needs_rebind_once = True

        fate = await harness.system.send(
            "c",
            text_message_content("switch here"),
            sender_label="owner",
            model_change="second-model",
        )

        assert fate == PromptDeliveryStarted()
        assert backend.session_starts == 2
        # The same conversation: the new child picked up the session it already had.
        assert backend.started_from_cursor == VENDOR_SESSION_CURSOR
        assert backend.model == "second-model"
        assert backend.written_texts() == ("first", "switch here")
        assert backend.sender_written_texts() == ("first", "switch here")

    _run(exercise)


def test_a_broken_claude_child_resumes_once_for_only_the_follow_up(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(
            harness,
            "c",
            backend_key=ConversationBackendKey.claude,
            model="current-model",
            reasoning_effort="high",
        )
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.fail_turn("c", "the terminal stream failed")
        backend = harness.backend("c")
        backend.needs_failed_child_recovery_once = True

        fate = await harness.system.send(
            "c", text_message_content("follow-up"), sender_label="owner"
        )

        assert fate == PromptDeliveryStarted()
        assert backend.lifecycle_events == [
            "start:None:current-model:high",
            "write:first",
            "stop",
            f"start:{VENDOR_SESSION_CURSOR}:current-model:high",
            "write:follow-up",
        ]
        assert backend.most_live_children_at_once == 1
        assert backend.written_texts() == ("first", "follow-up")
        assert await harness.recorded_prompts("c") == (
            ("first", "owner", "queue"),
            ("follow-up", "owner", "queue"),
        )
        assert not any(
            isinstance(event.payload, AgentMessageEventPayload)
            for event in await harness.events("c")
        )

    _run(exercise)


def test_a_rebind_whose_write_still_fails_changes_nothing(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c", backend_key=ConversationBackendKey.claude, model="start-model")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        backend = harness.backend("c")
        backend.needs_rebind_once = True
        backend.write_fails = True

        fate = await harness.system.send(
            "c", text_message_content("doomed"), sender_label="owner", model_change="second-model"
        )

        assert fate == PromptDeliveryRefused(
            refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
        )
        assert ConversationEventKind.model_changed not in await harness.recorded_kinds("c")
        assert ConversationEventKind.prompt_delivery_refused not in await harness.recorded_kinds(
            "c"
        )
        stored = await harness.store.read_conversation("c")
        assert stored is not None
        assert stored.model == "start-model"

    _run(exercise)


# --- permission asks ----------------------------------------------------------------------


def test_an_answer_the_backend_would_not_take_has_not_landed(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        ask_id = await harness.raise_permission_ask("c")
        harness.backend("c").permission_answer_write_fails = True

        assert await harness.system.answer_permission_ask("c", ask_id, "allow-once") is False

        assert ConversationEventKind.permission_answered not in await harness.recorded_kinds("c")
        # The ask is still waiting, so it can be answered again.
        assert await harness.system.has_pending_permission_ask("c") is True
        harness.backend("c").permission_answer_write_fails = False
        assert await harness.system.answer_permission_ask("c", ask_id, "deny") is True
        assert harness.backend("c").permission_answers == {ask_id: "deny"}

    _run(exercise)


def test_user_input_is_distinct_durable_and_recorded_only_after_delivery(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        request = await harness.request_user_input("c")

        requested = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, UserInputRequestedEventPayload)
        ]
        assert requested[0].questions == request.questions
        assert await harness.system.has_pending_user_input("c") is True
        assert await harness.system.has_pending_permission_ask("c") is False

        answers = (
            UserInputAnswer(question_id="scope", answers=("Backend", "Frontend")),
            UserInputAnswer(question_id="timing", answers=("Tomorrow",)),
        )
        harness.backend("c").user_input_answer_write_fails = True
        assert await harness.system.answer_user_input("c", request.request_id, answers) is False
        assert await harness.system.has_pending_user_input("c") is True
        assert not any(
            isinstance(event.payload, UserInputAnsweredEventPayload)
            for event in await harness.events("c")
        )

        harness.backend("c").user_input_answer_write_fails = False
        assert await harness.system.answer_user_input("c", request.request_id, answers) is True
        assert harness.backend("c").user_input_answers == {request.request_id: answers}
        assert await harness.system.has_pending_user_input("c") is False
        answered = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, UserInputAnsweredEventPayload)
        ]
        assert answered == [
            UserInputAnsweredEventPayload(request_id=request.request_id, answers=answers)
        ]

    _run(exercise)


def test_user_input_requires_the_complete_ordered_answer_map_and_dies_with_turn(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        request = await harness.request_user_input("c")

        incomplete = (UserInputAnswer(question_id="scope", answers=("Backend",)),)
        assert await harness.system.answer_user_input("c", request.request_id, incomplete) is False
        assert await harness.system.has_pending_user_input("c") is True

        await harness.system.interrupt("c")
        assert await harness.system.has_pending_user_input("c") is False
        assert (
            await harness.system.answer_user_input(
                "c",
                request.request_id,
                (
                    *incomplete,
                    UserInputAnswer(question_id="timing", answers=("Now",)),
                ),
            )
            is False
        )

    _run(exercise)


# --- is_running through the lifecycle -----------------------------------------------------


# --- saying the model is thinking ----------------------------------------------------------


def test_a_burst_of_thinking_is_one_pulse_and_then_a_pulse_now_and_then(
    harness: _Harness,
) -> None:
    """A pulse says the agent is alive. Hundreds of them say it hundreds of times.

    The first one goes out immediately — being prompt is the whole point — and the rest of
    the burst is dropped until the interval is up. Dropping them loses nothing: there is
    nothing in a pulse to lose.
    """

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        backend = harness.backend("c")
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        with harness.watch("c") as watching:
            for _ in range(50):
                await backend.sink.model_thinking_happened(token)
            assert await _thinking_pulses_now(watching) == 1

            # Not yet: a burst is one fact however long it goes on for.
            harness.clock.advance(MODEL_THINKING_PULSE_INTERVAL_SECONDS / 2)
            await backend.sink.model_thinking_happened(token)
            assert await _thinking_pulses_now(watching) == 0

            harness.clock.advance(MODEL_THINKING_PULSE_INTERVAL_SECONDS)
            await backend.sink.model_thinking_happened(token)
            assert await _thinking_pulses_now(watching) == 1

    _run(exercise)


def test_a_thought_from_a_turn_that_is_over_is_not_shown(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        backend = harness.backend("c")
        stale_token = backend.live_turn_token
        assert stale_token is not None and backend.sink is not None
        await harness.complete_turn("c")

        with harness.watch("c") as watching:
            await backend.sink.model_thinking_happened(stale_token)
            assert await _thinking_pulses_now(watching) == 0

    _run(exercise)


async def _thinking_pulses_now(watching: ConversationTailSubscription) -> int:
    """How many pulses are waiting for this watcher, taken until none is forthcoming.

    A watch carries committed rows as well as frames — a turn ending, the next prompt —
    and those are not what these tests are counting, so they are taken and passed over.
    """
    pulses = 0
    while True:
        try:
            item = await asyncio.wait_for(watching.next_item(), 0.02)
        except TimeoutError:
            return pulses
        if isinstance(item, ModelThinkingFrame):
            pulses += 1


# --- the role text ------------------------------------------------------------------------


def test_the_role_text_rides_the_very_first_prompt_and_only_that_one(harness: _Harness) -> None:
    """Composed by the core, so every backend is told what it is the same way."""

    async def exercise() -> None:
        await _start(
            harness,
            "c",
            role_materials=ConversationRoleMaterials(
                role_text="You are the Chief of Staff.",
                identity_environment_variables=(("PANELS_ROLE", "chief"),),
            ),
        )

        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        await harness.system.send("c", text_message_content("second"), sender_label="owner")

        assert harness.backend("c").written_texts() == (
            "You are the Chief of Staff.\n\nfirst",
            "second",
        )
        assert harness.backend("c").sender_written_texts() == ("first", "second")
        # The record keeps what the sender wrote: the role belongs to the conversation.
        assert await harness.recorded_prompts("c") == (
            ("first", "owner", "queue"),
            ("second", "owner", "queue"),
        )

    _run(exercise)


def test_a_refused_first_delivery_leaves_the_role_text_for_the_next_one(
    harness: _Harness,
) -> None:
    """The role rides the first prompt that is actually delivered, not the first attempt."""

    async def exercise() -> None:
        await _start(
            harness,
            "c",
            role_materials=ConversationRoleMaterials(role_text="You are the Chief of Staff."),
        )
        harness.backend("c").write_fails = True
        assert isinstance(
            await harness.system.send(
                "c",
                text_message_content("never got there"),
                sender_label="owner",
            ),
            PromptDeliveryRefused,
        )

        harness.backend("c").write_fails = False
        await harness.system.send(
            "c",
            text_message_content("after the refusal"),
            sender_label="owner",
        )

        assert harness.backend("c").written_texts() == (
            "You are the Chief of Staff.\n\nafter the refusal",
        )

    _run(exercise)


# --- the child's life ---------------------------------------------------------------------


def test_no_child_is_spawned_until_a_conversation_has_something_to_send(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        assert harness.spawned_conversation_ids == []
        assert await harness.system.is_running("c") is False

        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        assert harness.spawned_conversation_ids == ["c"]
        assert harness.backend("c").session_starts == 1
        assert harness.backend("c").started_from_cursor is None

    _run(exercise)


def test_a_session_cursor_the_backend_mints_is_kept(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.settle()

        stored = await harness.store.read_conversation("c")
        assert stored is not None
        assert stored.vendor_session_cursor == VENDOR_SESSION_CURSOR
        # It is the conversation's, not a row of its record.
        assert await harness.recorded_kinds("c") == (ConversationEventKind.prompt,)

    _run(exercise)


A_MENU = (
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.command,
        display_text="/review",
        insertion_text="/review ",
        description="Review the diff",
        argument_hint="[path]",
    ),
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.skill,
        display_text="$compact",
        insertion_text="$compact ",
        description="Summarise the conversation so far",
    ),
)
A_LATER_MENU = (
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.plugin,
        display_text="@compact",
        insertion_text="@compact exact ",
        description="Summarise the conversation so far",
    ),
)


def test_the_commands_a_backend_reports_are_kept_and_outlive_its_child(
    harness: _Harness,
) -> None:
    """The menu goes onto the conversation and stays there once the child is gone.

    Which commands an agent answers to is something that is true about it rather than
    something that happened in the conversation, so it leaves the record alone. Keeping it
    on the conversation is the whole point: the person who most needs the menu is the one
    opening a conversation to write into it, and nothing is running then.
    """

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        backend = harness.backend("c")
        assert backend.sink is not None
        await backend.sink.composer_catalog_reported(A_MENU)
        await harness.settle()
        await harness.complete_turn("c")

        harness.clock.advance(30 * 60 + 1)
        await harness.system._sweep_idle_children()
        assert await harness.system.is_running("c") is False

        stored = await harness.store.read_conversation("c")
        assert stored is not None
        assert stored.composer_catalog == A_MENU
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
        )

    _run(exercise)


def test_a_child_that_sat_idle_is_stopped_and_the_next_message_resumes_it(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        qualified_model = "openai-codex:gpt-5.6-sol"
        await _start(harness, "c", model=qualified_model)
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        backend = harness.backend("c")

        harness.clock.advance(30 * 60 + 1)
        await harness.system._sweep_idle_children()

        assert backend.stops == 1
        assert await harness.system.is_running("c") is False

        await harness.system.send("c", text_message_content("after the gap"), sender_label="owner")

        assert backend.session_starts == 2
        assert backend.started_from_cursor == VENDOR_SESSION_CURSOR
        assert backend.model == qualified_model
        assert backend.written_texts() == ("first", "after the gap")
        # Silent: the record says nothing about the child having gone away.
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.turn_ended,
            ConversationEventKind.prompt,
        )

    _run(exercise)


def test_a_child_that_is_working_or_freshly_used_is_left_alone(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "busy")
        await harness.system.send("busy", text_message_content("work"), sender_label="owner")
        await _start(harness, "recent")
        await harness.system.send("recent", text_message_content("work"), sender_label="owner")
        await harness.complete_turn("recent")

        harness.clock.advance(30 * 60 + 1)
        await harness.system.send("recent", text_message_content("just now"), sender_label="owner")
        await harness.complete_turn("recent")
        await harness.system._sweep_idle_children()

        assert harness.backend("busy").stops == 0
        assert harness.backend("recent").stops == 0

    _run(exercise)


def test_the_janitor_sweeps_on_its_own(tmp_path: Path) -> None:
    """The sweep is a task the system runs, not something a caller has to remember."""

    async def exercise() -> None:
        db_path = tmp_path / "janitor.db"
        conn = connect(str(db_path))
        create_schema(conn)
        conn.close()
        harness = _Harness(
            db_path,
            idle_child_stop_after_seconds=0.0,
            idle_child_sweep_interval_seconds=0.001,
        )
        try:
            await _start(harness, "c")
            await harness.system.send("c", text_message_content("first"), sender_label="owner")
            await harness.complete_turn("c")
            await harness.system.start_idle_child_janitor()

            for _ in range(200):
                if harness.backend("c").stops:
                    break
                await asyncio.sleep(0.005)
            assert harness.backend("c").stops == 1
        finally:
            await harness.system.shutdown()

    _run(exercise)


def test_idle_conversation_compacts_at_the_lower_window_boundary(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c", backend_key=ConversationBackendKey.codex)
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60 - 1)
        await harness.system._sweep_idle_children()
        assert harness.backend("c").written_texts() == ("first",)
        harness.clock.advance(1)
        await harness.system._sweep_idle_children()
        assert harness.backend("c").written_texts() == ("first", "/compact")
        assert harness.backend("c").automatic_compaction_writes == [False, True]

    _run(exercise)


def test_automatic_compaction_intent_survives_a_backend_rebind(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.backend("c").needs_rebind_once = True
        harness.clock.advance(50 * 60)

        await harness.system._sweep_idle_children()

        assert harness.backend("c").written_texts() == ("first", "/compact")
        assert harness.backend("c").automatic_compaction_writes == [False, True]
        assert harness.backend("c").session_starts == 2

    _run(exercise)


@pytest.mark.parametrize("model", ("fable[1m]", "opus[1m]"))
def test_confirmed_claude_compaction_waits_for_new_agent_activity_before_repeating(
    harness: _Harness, model: str
) -> None:
    async def exercise() -> None:
        await _start(
            harness,
            "c",
            backend_key=ConversationBackendKey.claude,
            model=model,
        )
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        await harness.confirm_compaction("c")
        await harness.complete_turn("c")
        record = await harness.store.read_conversation("c")
        assert record is not None
        assert record.automatically_compacted_through_sequence > 0
        assert (
            record.automatic_compaction_attempted_through_sequence
            == record.automatically_compacted_through_sequence
        )
        harness.clock.advance(50 * 60 + 1)
        await harness.system._sweep_idle_children()
        assert harness.backend("c").written_texts() == ("first", "/compact")
        await harness.system.send("c", text_message_content("ordinary"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        assert harness.backend("c").written_texts()[-2:] == ("ordinary", "/compact")

    _run(exercise)


def test_boundary_duplicate_waits_for_compaction_and_runs_once(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        content = text_message_content("at the boundary")
        first = await harness.system.send_with_receipt(
            "c", content, sender_label="owner", sender_message_id="same-id"
        )
        duplicate = await harness.system.send_with_receipt(
            "c", content, sender_label="owner", sender_message_id="same-id"
        )
        assert isinstance(first.fate, PromptDeliveryQueued)
        assert isinstance(duplicate.fate, PromptDeliveryQueued)
        assert first.newly_accepted
        assert not duplicate.newly_accepted
        assert harness.backend("c").written_texts() == ("first", "/compact")
        await harness.confirm_compaction("c")
        await harness.complete_turn("c")
        assert harness.backend("c").written_texts().count("at the boundary") == 1

    _run(exercise)


def test_send_now_does_not_cancel_automatic_compaction(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        fate = await harness.system.send(
            "c",
            text_message_content("urgent"),
            sender_label="owner",
            mode=PromptDeliveryMode.send_now,
        )
        assert isinstance(fate, PromptDeliveryQueued)
        assert harness.backend("c").cancellations == 0
        await harness.confirm_compaction("c")
        await harness.complete_turn("c")
        assert harness.backend("c").written_texts()[-1] == "urgent"

    _run(exercise)


def test_compaction_failure_releases_the_boundary_message_once(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        harness.backend("c").write_failures_remaining = 1
        await harness.system.send("c", text_message_content("after refusal"), sender_label="owner")
        assert harness.backend("c").written_texts() == ("first", "after refusal")

        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system.send(
            "c", text_message_content("after no boundary"), sender_label="owner"
        )
        await harness.complete_turn("c")
        assert harness.backend("c").written_texts().count("after no boundary") == 1
        endings = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, TurnEndedEventPayload)
        ]
        assert endings[-1].automatic_compaction_result is AutomaticCompactionResult.not_compacted

    _run(exercise)


@pytest.mark.parametrize(
    "backend_text",
    ("Not enough messages to compact.", None),
    ids=("claude-refusal", "silent-completion"),
)
def test_completed_unconfirmed_compaction_records_a_terminal_attempt_and_retries_new_activity(
    harness: _Harness, backend_text: str | None
) -> None:
    async def exercise() -> None:
        await _start(
            harness,
            "c",
            backend_key=ConversationBackendKey.claude,
            model="opus[1m]",
        )
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        before = await harness.store.read_conversation("c")
        assert before is not None
        activity_sequence = before.latest_agent_activity_sequence
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        if backend_text is not None:
            await harness.agent_message("c", text_message_content(backend_text))
        await harness.complete_turn("c")

        after = await harness.store.read_conversation("c")
        assert after is not None
        assert after.automatically_compacted_through_sequence == 0
        assert after.automatic_compaction_attempted_through_sequence == activity_sequence
        endings = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, TurnEndedEventPayload)
        ]
        assert endings[-1] == TurnEndedEventPayload(
            ending=ConversationTurnEnding.completed,
            automatic_compaction_result=AutomaticCompactionResult.not_compacted,
        )

        harness.clock.advance(5 * 60)
        await harness.system._sweep_idle_children()
        assert harness.backend("c").written_texts() == ("first", "/compact")

        await harness.system.send("c", text_message_content("later"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        assert harness.backend("c").written_texts()[-2:] == ("later", "/compact")

    _run(exercise)


@pytest.mark.parametrize(
    "ending", (ConversationTurnEnding.failed, ConversationTurnEnding.interrupted)
)
def test_failed_or_interrupted_compaction_remains_retryable(
    harness: _Harness, ending: ConversationTurnEnding
) -> None:
    async def exercise() -> None:
        await _start(harness, "c", backend_key=ConversationBackendKey.claude)
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        await harness._end_turn("c", ending, "provider failed" if ending == "failed" else None)

        record = await harness.store.read_conversation("c")
        assert record is not None
        assert record.automatic_compaction_attempted_through_sequence == 0
        harness.clock.advance(5 * 60)
        await harness.system._sweep_idle_children()
        assert harness.backend("c").written_texts() == ("first", "/compact", "/compact")

    _run(exercise)


@pytest.mark.parametrize("confirmed", (True, False), ids=("confirmed", "not-compacted"))
def test_maintenance_releases_owner_and_worker_messages_in_order(
    harness: _Harness, confirmed: bool
) -> None:
    async def exercise() -> None:
        await _start(harness, "c", backend_key=ConversationBackendKey.claude)
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        ticket = Principal(PrincipalKind.ticket, "t_worker")
        await harness.system.send(
            "c",
            text_message_content("owner message"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=ticket,
        )
        await harness.system.send(
            "c",
            text_message_content("worker opener"),
            sender_label="Ticket t_worker",
            sender=ticket,
            recipient=OWNER_PRINCIPAL,
        )

        if confirmed:
            await harness.confirm_compaction("c")
        await harness.complete_turn("c")
        released = harness.backend("c").written_texts()[-1]
        assert released == "owner message\n\nTicket t_worker:\nworker opener"
        assert released.count("owner message") == 1
        assert released.count("worker opener") == 1
        await harness.complete_turn("c")

        prompts = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, PromptEventPayload)
        ]
        assert [message_content_text(prompt.content) for prompt in prompts[-2:]] == [
            "owner message",
            "worker opener",
        ]
        assert [prompt.sender for prompt in prompts[-2:]] == [OWNER_PRINCIPAL, ticket]

    _run(exercise)


def test_restart_keeps_a_completed_compaction_attempt_from_repeating(tmp_path: Path) -> None:
    async def exercise() -> None:
        db_path = tmp_path / "restart-after-attempt.db"
        conn = connect(str(db_path))
        create_schema(conn)
        conn.close()
        clock = _FakeMonotonicClock()
        first = _Harness(db_path, clock=clock)
        await _start(first, "c", backend_key=ConversationBackendKey.claude)
        await first.system.send("c", text_message_content("first"), sender_label="owner")
        await first.complete_turn("c")
        clock.advance(50 * 60)
        await first.system._sweep_idle_children()
        await first.complete_turn("c")
        before = await first.store.read_conversation("c")
        assert before is not None
        assert before.automatic_compaction_attempted_through_sequence > 0
        await first.system.shutdown()

        clock.advance(5 * 60)
        restarted = _Harness(db_path, clock=clock)
        try:
            await restarted.system._sweep_idle_children()
            assert restarted.spawned_conversation_ids == []
            after = await restarted.store.read_conversation("c")
            assert after == before
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_restart_sweep_recovers_an_unloaded_due_conversation(tmp_path: Path) -> None:
    async def exercise() -> None:
        db_path = tmp_path / "restart.db"
        conn = connect(str(db_path))
        create_schema(conn)
        conn.close()
        clock = _FakeMonotonicClock()
        first = _Harness(db_path, clock=clock)
        await _start(first, "c")
        await first.system.send("c", text_message_content("first"), sender_label="owner")
        await first.complete_turn("c")
        await first.system.shutdown()
        clock.advance(50 * 60)
        restarted = _Harness(db_path, clock=clock)
        try:
            await restarted.system._sweep_idle_children()
            assert restarted.backend("c").written_texts() == ("/compact",)
            assert restarted.spawned_conversation_ids == ["c"]
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_late_backend_prose_remains_runtime_only(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        backend = harness.backend("c")
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await harness.complete_turn("c")
        before = await harness.store.read_conversation("c")
        events_before = await harness.events("c")
        assert before is not None
        await backend.sink.agent_message_completed(token, text_message_content("late"))
        await harness.settle()
        after = await harness.store.read_conversation("c")
        assert after is not None
        assert after.latest_agent_activity_at == before.latest_agent_activity_at
        assert after.latest_agent_activity_sequence == before.latest_agent_activity_sequence
        assert await harness.events("c") == events_before

    _run(exercise)


def test_uncertain_automatic_compaction_discards_its_child_before_message_release(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        real_append = harness.store.append_delivered_prompt

        async def fail_compaction_record(conversation_id: str, **kwargs):  # type: ignore[no-untyped-def]
            if message_content_text(kwargs["prompt"].content) == "/compact":
                raise sqlite3.OperationalError("record unavailable")
            return await real_append(conversation_id, **kwargs)

        harness.store.append_delivered_prompt = fail_compaction_record  # type: ignore[method-assign]
        await harness.system.send(
            "c", text_message_content("after uncertain wire"), sender_label="owner"
        )

        lifecycle = harness.backend("c").lifecycle_events
        compact_write = lifecycle.index("write:/compact")
        child_stop = lifecycle.index("stop", compact_write)
        user_write = lifecycle.index("write:after uncertain wire", child_stop)
        assert compact_write < child_stop < user_write
        assert harness.backend("c").session_starts == 2

    _run(exercise)


def test_uncertain_compaction_keeps_message_held_when_child_stop_fails(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        real_append = harness.store.append_delivered_prompt

        async def fail_compaction_record(conversation_id: str, **kwargs):  # type: ignore[no-untyped-def]
            if message_content_text(kwargs["prompt"].content) == "/compact":
                raise sqlite3.OperationalError("record unavailable")
            return await real_append(conversation_id, **kwargs)

        harness.store.append_delivered_prompt = fail_compaction_record  # type: ignore[method-assign]
        backend = harness.backend("c")
        backend.stop_raises_something_unnamed = True
        await harness.system.send("c", text_message_content("must stay held"), sender_label="owner")

        assert backend.written_texts() == ("first", "/compact")
        assert backend.live_children == 1
        waiting = await harness.system.held_prompts("c")
        assert [message_content_text(item.content) for item in waiting] == ["must stay held"]
        direct = await harness.system.send(
            "c",
            text_message_content("direct send-now"),
            sender_label="owner",
            mode=PromptDeliveryMode.send_now,
        )
        assert direct == PromptDeliveryQueued(queue_position=2)
        promoted = await harness.system.promote_held_prompt(
            "c", waiting[0].held_prompt_id, HeldPromptPromotionMode.send_now
        )
        assert promoted == PromptDeliveryQueued(queue_position=1)
        assert backend.written_texts() == ("first", "/compact")
        assert backend.cancellations == 0
        backend.stop_raises_something_unnamed = False

    _run(exercise)


def test_dequeued_sender_identity_stays_admitted_until_the_prompt_row_exists(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        content = text_message_content("held once")
        await harness.system.send("c", content, sender_label="owner", sender_message_id="held-race")
        backend = harness.backend("c")
        backend.write_has_begun = asyncio.Event()
        backend.writes_wait_for_release = asyncio.Event()

        ending = asyncio.create_task(harness.complete_turn("c"))
        await backend.write_has_begun.wait()
        duplicate = asyncio.create_task(
            harness.system.send_with_receipt(
                "c", content, sender_label="owner", sender_message_id="held-race"
            )
        )
        await asyncio.sleep(0)
        assert not duplicate.done()
        backend.writes_wait_for_release.set()
        await ending
        duplicate_receipt = await duplicate
        assert duplicate_receipt.fate == PromptDeliveryStarted()
        assert not duplicate_receipt.newly_accepted
        assert backend.written_texts().count("held once") == 1

    _run(exercise)


def test_promoted_pre_wire_exception_releases_sender_identity_for_safe_retry(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        content = text_message_content("promoted retry")
        await harness.system.send(
            "c", content, sender_label="owner", sender_message_id="promoted-id"
        )
        selected = (await harness.system.held_prompts("c"))[0]
        backend = harness.backend("c")
        backend.writes_raise_something_unnamed = True

        with pytest.raises(RuntimeError, match="adapter fell over"):
            await harness.system.promote_held_prompt(
                "c", selected.held_prompt_id, HeldPromptPromotionMode.send_now
            )

        backend.writes_raise_something_unnamed = False
        fate = await harness.system.send(
            "c", content, sender_label="owner", sender_message_id="promoted-id"
        )
        assert fate == PromptDeliveryStarted()
        assert backend.written_texts().count("promoted retry") == 1

    _run(exercise)


def test_promoted_send_now_stays_held_behind_automatic_compaction(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        await harness.system._sweep_idle_children()
        await harness.system.send("c", text_message_content("held"), sender_label="owner")
        selected = (await harness.system.held_prompts("c"))[0]

        fate = await harness.system.promote_held_prompt(
            "c", selected.held_prompt_id, HeldPromptPromotionMode.send_now
        )

        assert fate == PromptDeliveryQueued(queue_position=1)
        assert harness.backend("c").cancellations == 0
        waiting = await harness.system.held_prompts("c")
        assert [message_content_text(item.content) for item in waiting] == ["held"]

    _run(exercise)


def test_shutting_down_stops_every_child(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "first")
        await _start(harness, "second")
        await harness.system.send("first", text_message_content("work"), sender_label="owner")
        await harness.system.send("second", text_message_content("work"), sender_label="owner")

        await harness.system.shutdown()

        assert harness.backend("first").stops == 1
        assert harness.backend("second").stops == 1

    _run(exercise)


def test_after_a_restart_nothing_is_running_and_the_next_message_resumes(
    harness: _Harness, tmp_path: Path
) -> None:
    """No re-attach on boot: the record is all there and the session is picked up lazily."""

    async def exercise() -> None:
        qualified_model = "openai-codex:gpt-5.6-sol"
        await _start(harness, "c", model=qualified_model)
        await harness.system.send(
            "c",
            text_message_content("before the restart"),
            sender_label="owner",
        )
        await harness.settle()
        await harness.system.shutdown()

        restarted = _Harness(tmp_path / "conversations.db")
        restarted.backends = harness.backends
        try:
            assert await restarted.system.is_running("c") is False
            assert await restarted.system.has_pending_permission_ask("c") is False

            fate = await restarted.system.send(
                "c",
                text_message_content("after the restart"),
                sender_label="owner",
            )

            assert fate == PromptDeliveryStarted()
            assert restarted.backend("c").started_from_cursor == VENDOR_SESSION_CURSOR
            assert restarted.backend("c").model == qualified_model
            assert await restarted.system.is_running("c") is True
            assert [str(event.kind) for event in await restarted.events("c")] == [
                "prompt",
                "prompt",
            ]
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_a_stored_legacy_bare_hermes_model_is_passed_through_on_restart(
    harness: _Harness, tmp_path: Path
) -> None:
    """Catalog qualification changes new choices, not existing conversation records."""

    async def exercise() -> None:
        legacy_model = "legacy-hermes-model"
        await _start(harness, "legacy", model=legacy_model)
        await harness.system.send(
            "legacy",
            text_message_content("before the restart"),
            sender_label="owner",
        )
        await harness.settle()
        await harness.system.shutdown()

        restarted = _Harness(tmp_path / "conversations.db")
        restarted.backends = harness.backends
        try:
            fate = await restarted.system.send(
                "legacy",
                text_message_content("after the restart"),
                sender_label="owner",
            )

            assert fate == PromptDeliveryStarted()
            assert restarted.backend("legacy").started_from_cursor == VENDOR_SESSION_CURSOR
            assert restarted.backend("legacy").model == legacy_model
        finally:
            await restarted.system.shutdown()

    _run(exercise)


# --- killing a conversation's activity ----------------------------------------------------


def test_nothing_runs_after_a_kill(harness: _Harness) -> None:
    """The difference from an interrupt: the queue is silenced rather than let run."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("held"), sender_label="owner")

        await harness.system.kill("c")
        await harness.settle()

        assert harness.backend("c").written_texts() == ("incumbent",)
        assert await harness.system.is_running("c") is False

    _run(exercise)


def test_a_kill_waits_for_a_turn_that_is_still_being_started(harness: _Harness) -> None:
    """A kill cannot stop a turn that has not started yet, so it waits for the one on
    its way and stops that."""

    async def exercise() -> None:
        await _start(harness, "c")
        # A turn first, so the child exists and the send below waits only at the write.
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        backend = harness.backend("c")
        backend.writes_wait_for_release = asyncio.Event()
        backend.write_has_begun = asyncio.Event()

        sending = asyncio.create_task(
            harness.system.send("c", text_message_content("starting"), sender_label="owner")
        )
        await backend.write_has_begun.wait()
        killing = asyncio.create_task(harness.system.kill("c"))
        for _ in range(_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP):
            await asyncio.sleep(0)

        # The kill is waiting rather than racing: the turn is neither started nor killed.
        assert not killing.done()
        backend.writes_wait_for_release.set()

        assert await sending == PromptDeliveryStarted()
        await killing
        await harness.settle()

        assert await harness.system.is_running("c") is False
        assert backend.cancellations == 1
        assert await harness.recorded_endings("c") == (
            ConversationTurnEnding.completed,
            ConversationTurnEnding.interrupted,
        )

    _run(exercise)


def test_a_message_sent_while_a_kill_is_running_is_not_swallowed_by_it(
    harness: _Harness,
) -> None:
    """A kill holds the conversation still, so a send either loses its message to the
    kill or starts a turn — never waits behind a queue nothing will ever drain."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        backend.cancels_wait_for_release = asyncio.Event()
        backend.cancel_has_begun = asyncio.Event()

        killing = asyncio.create_task(harness.system.kill("c"))
        await backend.cancel_has_begun.wait()
        sending = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("sent during the kill"),
                sender_label="owner",
            )
        )
        for _ in range(_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP):
            await asyncio.sleep(0)
        backend.cancels_wait_for_release.set()

        await killing
        assert await sending == PromptDeliveryStarted()
        await harness.settle()

        assert await harness.system.is_running("c") is True
        assert backend.written_texts() == ("incumbent", "sent during the kill")

    _run(exercise)


def test_an_adapter_that_falls_over_in_a_way_it_never_named_leaves_the_conversation_usable(
    harness: _Harness,
) -> None:
    """An unnamed failure is a fault in the adapter, not a delivery impossibility.

    It is not turned into a refusal — the caller gets the fault itself — but the
    conversation is not left half-way through starting a turn that will never start.
    """

    async def exercise() -> None:
        await _start(harness, "c")
        harness.backend("c").writes_raise_something_unnamed = True

        with pytest.raises(RuntimeError, match="the adapter fell over"):
            await harness.system.send("c", text_message_content("doomed"), sender_label="owner")

        assert await harness.system.is_running("c") is False
        harness.backend("c").writes_raise_something_unnamed = False
        assert await harness.system.send(
            "c",
            text_message_content("after the fault"),
            sender_label="owner",
        ) == (PromptDeliveryStarted())
        assert harness.backend("c").written_texts() == ("after the fault",)

    _run(exercise)


def test_a_held_message_that_cannot_be_delivered_is_recorded_and_the_line_carries_on(
    harness: _Harness,
) -> None:
    """One message nobody can deliver must not take the rest of the line with it.

    The failing message is written down as thrown away rather than vanishing, and the
    messages behind it still get their turn. Before this the drain gave up on the whole
    line and left the conversation idle with nothing coming back for it.
    """

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("doomed"), sender_label="owner")
        harness.backend("c").writes_raise_something_unnamed = True

        await harness.complete_turn("c")

        assert await harness.system.held_prompts("c") == ()
        discarded = [
            message_content_text(event.payload.content)
            for event in await harness.events("c")
            if isinstance(event.payload, PromptDiscardedEventPayload)
        ]
        assert discarded == ["doomed"]

        # The conversation is usable, and a message sent now runs rather than joining a
        # line nothing is emptying.
        harness.backend("c").writes_raise_something_unnamed = False
        assert await harness.system.send(
            "c",
            text_message_content("after the fault"),
            sender_label="owner",
        ) == (PromptDeliveryStarted())

    _run(exercise)


def test_everything_waiting_goes_in_as_one_turn_with_a_row_for_each_sender(
    harness: _Harness,
) -> None:
    """The agent is given one prompt, and the record still names every sender."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        for index, label in enumerate(("owner", "loop", "owner"), start=1):
            await harness.system.send(
                "c",
                text_message_content(f"waiting-{index}"),
                sender_label=label,
                sender_message_id=f"sender-{index}",
            )

        await harness.complete_turn("c")

        assert harness.backend("c").written_texts() == (
            "incumbent",
            "waiting-1\n\nloop:\nwaiting-2\n\nowner:\nwaiting-3",
        )
        prompts = [
            (event.payload.sender_message_id, message_content_text(event.payload.content))
            for event in await harness.events("c")
            if isinstance(event.payload, PromptEventPayload)
        ]
        assert prompts[1:] == [
            ("sender-1", "waiting-1"),
            ("sender-2", "waiting-2"),
            ("sender-3", "waiting-3"),
        ]

    _run(exercise)


def test_a_waiting_message_that_names_another_model_starts_its_own_turn(
    harness: _Harness,
) -> None:
    """A turn runs on one model, so the run stops before a message that names another."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("plain"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("on another model"),
            sender_label="owner",
            model_change="other-model",
        )
        await harness.system.send("c", text_message_content("after it"), sender_label="owner")

        await harness.complete_turn("c")
        assert harness.backend("c").written_texts() == ("incumbent", "plain")

        await harness.complete_turn("c")
        assert harness.backend("c").written_texts() == (
            "incumbent",
            "plain",
            "on another model\n\nowner:\nafter it",
        )
        assert harness.backend("c").model == "other-model"

    _run(exercise)


def test_a_held_message_whose_adapter_falls_over_leaves_the_conversation_usable(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("held"), sender_label="owner")
        harness.backend("c").writes_raise_something_unnamed = True

        await harness.complete_turn("c")

        assert await harness.system.is_running("c") is False
        harness.backend("c").writes_raise_something_unnamed = False
        assert await harness.system.send(
            "c",
            text_message_content("after the fault"),
            sender_label="owner",
        ) == (PromptDeliveryStarted())

    _run(exercise)


# --- an ending is one ending, and a cancel that failed is not a cancel --------------------


def test_a_backend_reporting_its_own_ending_during_a_cancel_does_not_record_a_second_one(
    harness: _Harness,
) -> None:
    """A turn has one ending. The core asked for this one, so the core's is the one that
    is written — an interruption, which is what actually happened to the turn."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        # Let the queue finish with the session cursor first: busy on that, it could not
        # get to the ending in time and the race the fix is for would not happen.
        await harness.settle()
        harness.backend("c").reports_its_ending_during_a_cancel = True

        await harness.system.interrupt("c")
        await harness.settle()

        assert await harness.recorded_endings("c") == (ConversationTurnEnding.interrupted,)
        assert await harness.system.is_running("c") is False

    _run(exercise)


def test_a_cancel_that_never_reached_the_child_still_records_the_interruption(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """The ending is the core's and it stands. But a child that cannot be told to stop
    cannot be trusted to be told anything, so it is thrown away and the next message
    starts a fresh one."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        # Let the session cursor the child minted reach the record, so the respawn below
        # is asked to resume rather than to start something new.
        await harness.settle()
        backend = harness.backend("c")
        backend.cancels_raise_something_unnamed = True

        with caplog.at_level(logging.ERROR, logger="planner.conversation"):
            await harness.system.interrupt("c")

        assert await harness.recorded_endings("c") == (ConversationTurnEnding.interrupted,)
        assert await harness.system.is_running("c") is False
        # Thrown away: stopped, and no longer the conversation's child.
        assert backend.stops == 1
        assert backend.most_live_children_at_once == 1

        lines = [
            record.getMessage()
            for record in caplog.records
            if record.name == "planner.conversation"
        ]
        assert len(lines) == 1
        assert "conversation turn cancel failed" in lines[0]
        assert "conversation_id=c" in lines[0]

        backend.cancels_raise_something_unnamed = False
        assert await harness.system.send(
            "c",
            text_message_content("after the bad cancel"),
            sender_label="owner",
        ) == (PromptDeliveryStarted())
        assert backend.session_starts == 2
        assert backend.started_from_cursor == VENDOR_SESSION_CURSOR
        assert backend.most_live_children_at_once == 1

    _run(exercise)


# --- one agent per conversation, even mid-sweep -------------------------------------------


def test_a_message_arriving_while_the_janitor_stops_a_child_never_sees_two(
    harness: _Harness,
) -> None:
    """Forgetting the child and stopping it are one act. Done separately, the message
    below spawns its own child while the one being stopped is still alive."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        backend = harness.backend("c")
        backend.stops_wait_for_release = asyncio.Event()
        backend.stop_has_begun = asyncio.Event()
        harness.clock.advance(30 * 60 + 1)

        sweeping = asyncio.create_task(harness.system._sweep_idle_children())
        await backend.stop_has_begun.wait()
        sending = asyncio.create_task(
            harness.system.send("c", text_message_content("during the sweep"), sender_label="owner")
        )
        for _ in range(_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP):
            await asyncio.sleep(0)

        # The message is waiting for the sweep to finish, not spawning beside it.
        assert backend.session_starts == 1
        backend.stops_wait_for_release.set()

        await sweeping
        assert await sending == PromptDeliveryStarted()
        assert backend.session_starts == 2
        assert backend.most_live_children_at_once == 1

    _run(exercise)


# --- live frames belong to a turn -----------------------------------------------------------


def test_a_frame_from_a_turn_the_conversation_has_moved_on_from_is_not_shown(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        live_tail = ConversationLiveTail()
        harness.system._live_tail = live_tail
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        backend = harness.backend("c")
        stale_token = backend.live_turn_token
        assert stale_token is not None and backend.sink is not None
        await harness.complete_turn("c")
        await harness.system.send("c", text_message_content("second"), sender_label="owner")
        live_token = backend.live_turn_token
        assert live_token is not None

        with live_tail.subscribe("c") as watching:
            await backend.sink.agent_message_delta(stale_token, "from the turn before")
            await backend.sink.tool_call_progress(
                stale_token, tool_call_id="call-old", detail="still going"
            )
            await backend.sink.agent_message_delta(live_token, "from the turn running now")

            # The dead turn's frames were never shown, so the first thing a watcher sees
            # is the live turn's. Published, they would be sitting in front of it.
            first_shown = await watching.next_item()

        assert isinstance(first_shown, AgentMessageDeltaFrame)
        assert first_shown.text_delta == "from the turn running now"

    _run(exercise)


# --- starting a conversation a message is already using -------------------------------------


def test_starting_a_conversation_never_replaces_one_a_message_is_already_using(
    harness: _Harness,
) -> None:
    """The row exists the moment it is committed, so a send can pick the conversation up
    before start_conversation has finished. Replacing its state would strand a live child
    that nothing could reach again."""

    async def exercise() -> None:
        real_create = harness.store.create_conversation
        row_is_committed = asyncio.Event()
        let_start_conversation_finish = asyncio.Event()

        async def gated_create(resolved):  # type: ignore[no-untyped-def]
            record = await real_create(resolved)
            row_is_committed.set()
            await let_start_conversation_finish.wait()
            return record

        harness.store.create_conversation = gated_create  # type: ignore[method-assign]

        starting = asyncio.create_task(_start(harness, "c"))
        await row_is_committed.wait()

        # A message arrives for a conversation whose row is there, and runs all the way.
        assert await harness.system.send(
            "c",
            text_message_content("first"),
            sender_label="owner",
        ) == (PromptDeliveryStarted())

        let_start_conversation_finish.set()
        await starting

        assert await harness.system.is_running("c") is True
        assert harness.backend("c").written_texts() == ("first",)
        assert harness.backend("c").session_starts == 1

    _run(exercise)


# --- a turn that ended and could not be written down -----------------------------------------


def test_a_failed_turn_whose_ending_cannot_be_written_still_says_so_in_the_log(
    harness: _Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """The record and the log are the two ways anyone finds out. Losing the row must not
    cost the line as well, or a turn ends and leaves no trace anywhere."""

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")

        real_append = harness.store.append_turn_ending

        async def append_that_cannot_write_an_ending(  # type: ignore[no-untyped-def]
            conversation_id: str, payloads, **kwargs
        ):
            if any(isinstance(payload, TurnEndedEventPayload) for payload in payloads):
                raise sqlite3.OperationalError("database is locked")
            return await real_append(conversation_id, payloads, **kwargs)

        harness.store.append_turn_ending = append_that_cannot_write_an_ending  # type: ignore[method-assign]

        with caplog.at_level(logging.ERROR, logger="planner.conversation"):
            await harness.fail_turn(
                "c", error_summary="the model refused", standard_error_tail="stderr tail"
            )

        lines = [
            record.getMessage()
            for record in caplog.records
            if record.name == "planner.conversation"
        ]
        # One line for the ending that could not be recorded, and the failed turn's own
        # line all the same — saying there is no row to go and look at.
        assert len(lines) == 2
        assert "conversation turn ending could not be recorded" in lines[0]
        assert "ending=failed" in lines[0]
        assert "conversation turn failed" in lines[1]
        assert "sequence=None" in lines[1]
        assert "the model refused" in lines[1]

        assert await harness.recorded_endings("c") == ()
        # The turn is over even though its ending is not written down.
        assert await harness.system.is_running("c") is False

    _run(exercise)


def test_the_system_refuses_to_be_built_without_a_factory_for_every_backend(
    harness: _Harness,
) -> None:
    with pytest.raises(ValueError, match="backend child factory"):
        SqliteProcessConversationSystem(
            store=harness.store,
            message_files=harness.message_files,
            backend_child_factories={ConversationBackendKey.hermes: harness._make_child},
        )


# --- taking one waiting message back -------------------------------------------------------


def test_one_waiting_message_can_be_taken_back_and_the_rest_still_run(
    harness: _Harness,
) -> None:
    """A held message is this system's own, so taking it back reaches no backend at all."""

    async def exercise() -> None:
        await _start(harness, "c")
        sender = Principal(PrincipalKind.ticket, "t_sender")
        recipient = Principal(PrincipalKind.chief, "chief")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("first held"),
            sender_label="owner",
            sender_message_id="message-one",
            sent_at_unix_milliseconds=1234,
            sender=sender,
            recipient=recipient,
        )
        await harness.system.send(
            "c",
            text_message_content("second held"),
            sender_label="owner",
            sender_message_id="message-two",
        )

        held = await harness.system.held_prompts("c")
        assert await harness.system.discard_held_prompt("c", held[0].held_prompt_id) is True
        assert len(await harness.system.held_prompts("c")) == 1
        # Nothing was asked of the backend: a waiting message had never reached it.
        assert harness.backend("c").written_texts() == ("incumbent",)
        assert harness.backend("c").cancellations == 0

        # The one that was taken back is written down as discarded, under the name its
        # sender gave it, before the one that survived it runs.
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.prompt_discarded,
        )
        discarded = [
            (
                message_content_text(payload.content),
                payload.sender_message_id,
                payload.sent_at_unix_milliseconds,
                payload.sender,
                payload.recipient,
            )
            for payload in (
                event.payload
                for event in await harness.events("c")
                if isinstance(event.payload, PromptDiscardedEventPayload)
            )
        ]
        assert discarded == [("first held", "message-one", 1234, sender, recipient)]

        await harness.complete_turn("c")

        assert harness.backend("c").written_texts() == ("incumbent", "second held")

    _run(exercise)


def test_promoted_send_now_claims_one_message_and_preserves_fifo(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("selected"),
            sender_label="owner",
            model_change="selected-model",
        )
        await harness.system.send("c", text_message_content("last"), sender_label="owner")
        held = await harness.system.held_prompts("c")

        fate = await harness.system.promote_held_prompt(
            "c", held[1].held_prompt_id, HeldPromptPromotionMode.send_now
        )

        assert fate == PromptDeliveryStarted()
        assert harness.backend("c").written_texts() == ("incumbent", "selected")
        assert harness.backend("c").model == "selected-model"
        waiting = await harness.system.held_prompts("c")
        assert [message_content_text(item.content) for item in waiting] == ["first", "last"]
        assert (
            await harness.system.promote_held_prompt(
                "c", held[1].held_prompt_id, HeldPromptPromotionMode.send_now
            )
            is None
        )

        # What is left of the line keeps its order and runs as one turn.
        await harness.complete_turn("c")
        assert harness.backend("c").written_texts() == (
            "incumbent",
            "selected",
            "first\n\nowner:\nlast",
        )

    _run(exercise)


def test_refused_promoted_send_now_is_recorded_and_drains_the_fifo(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("selected"),
            sender_label="owner",
            sender_message_id="selected-sender-id",
            model_change="never-model",
        )
        await harness.system.send("c", text_message_content("next"), sender_label="owner")
        selected = (await harness.system.held_prompts("c"))[0]
        harness.backend("c").write_failures_remaining = 1

        fate = await harness.system.promote_held_prompt(
            "c", selected.held_prompt_id, HeldPromptPromotionMode.send_now
        )

        assert fate == PromptDeliveryRefused(
            refusal_reason=PromptDeliveryRefusalReason.write_to_backend_failed
        )
        assert harness.backend("c").cancellations == 1
        assert harness.backend("c").model != "never-model"
        assert harness.backend("c").written_texts() == ("incumbent", "next")
        refusals = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, PromptDeliveryRefusedEventPayload)
        ]
        assert len(refusals) == 1
        assert refusals[0].mode is PromptDeliveryMode.send_now
        assert refusals[0].sender_message_id == "selected-sender-id"

    _run(exercise)


def test_two_promotions_of_one_held_id_have_exactly_one_winner(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("selected"), sender_label="owner")
        selected = (await harness.system.held_prompts("c"))[0]

        results = await asyncio.gather(
            harness.system.promote_held_prompt(
                "c", selected.held_prompt_id, HeldPromptPromotionMode.send_now
            ),
            harness.system.promote_held_prompt(
                "c", selected.held_prompt_id, HeldPromptPromotionMode.send_now
            ),
        )

        assert results.count(PromptDeliveryStarted()) == 1
        assert results.count(None) == 1
        assert harness.backend("c").written_texts().count("selected") == 1

    _run(exercise)


def test_promoted_steer_does_not_apply_the_queued_model_change(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("steered"),
            sender_label="owner",
            model_change="never-model",
        )
        held = (await harness.system.held_prompts("c"))[0]

        fate = await harness.system.promote_held_prompt(
            "c", held.held_prompt_id, HeldPromptPromotionMode.steer
        )

        assert fate == PromptDeliveryInjected()
        assert harness.backend("c").model != "never-model"
        assert harness.backend("c").writes[-1] == _FakeBackendWrite(
            content=text_message_content("steered"), steered=True
        )
        assert ConversationEventKind.model_changed not in await harness.recorded_kinds("c")
        assert harness.backend("c").steer_tokens == [TurnToken("c", 1)]

    _run(exercise)


def test_uncertain_promoted_steer_settles_only_the_selected_held_row(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("selected"),
            sender_label="owner",
            sender_message_id="selected-id",
            model_change="never-model",
        )
        await harness.system.send("c", text_message_content("next"), sender_label="owner")
        selected = (await harness.system.held_prompts("c"))[0]
        backend = harness.backend("c")
        backend.steer_outcome = BackendSteerUncertain()

        fate = await harness.system.promote_held_prompt(
            "c", selected.held_prompt_id, HeldPromptPromotionMode.steer
        )

        assert fate == PromptDeliveryUncertain()
        assert backend.model != "never-model"
        assert backend.steer_tokens == [TurnToken("c", 1)]
        assert [
            message_content_text(item.content) for item in await harness.system.held_prompts("c")
        ] == ["next"]
        uncertain_rows = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, PromptDeliveryUncertainEventPayload)
        ]
        assert len(uncertain_rows) == 1
        assert uncertain_rows[0].sender_message_id == "selected-id"

    _run(exercise)


@pytest.mark.parametrize(
    ("steer_outcome", "expected_fate"),
    [
        (BackendSteerAccepted(), PromptDeliveryInjected()),
        (BackendSteerUncertain(), PromptDeliveryUncertain()),
    ],
)
def test_a_promoted_steer_credits_its_sender_while_the_target_turn_remains_active(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    steer_outcome: BackendSteerOutcome,
    expected_fate: PromptDeliveryInjected | PromptDeliveryUncertain,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        recipient = Principal(PrincipalKind.ticket, "t_worker")
        chief = Principal(PrincipalKind.chief, "chief")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("addressed promotion"),
            sender_label="Chief",
            sender=chief,
            recipient=recipient,
        )
        selected = (await harness.system.held_prompts("c"))[0]
        harness.backend("c").steer_outcome = steer_outcome

        assert (
            await harness.system.promote_held_prompt(
                "c", selected.held_prompt_id, HeldPromptPromotionMode.steer
            )
            == expected_fate
        )
        await harness.complete_turn("c")

        markers = [
            event.payload.prompt_sender
            for event in await harness.events("c")
            if isinstance(event.payload, ExplicitReplyMissingEventPayload)
        ]
        assert markers == [chief]

    _run(exercise)


@pytest.mark.parametrize(
    ("steer_outcome", "expected_fate", "recorded_payload_type"),
    [
        (BackendSteerAccepted(), PromptDeliveryInjected(), PromptEventPayload),
        (BackendSteerUncertain(), PromptDeliveryUncertain(), PromptDeliveryUncertainEventPayload),
    ],
)
def test_a_delayed_promoted_steer_receipt_does_not_credit_its_sender_to_the_replacement_turn(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    steer_outcome: BackendSteerOutcome,
    expected_fate: PromptDeliveryInjected | PromptDeliveryUncertain,
    recorded_payload_type: type[PromptEventPayload] | type[PromptDeliveryUncertainEventPayload],
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        recipient = Principal(PrincipalKind.ticket, "t_worker")
        chief = Principal(PrincipalKind.chief, "chief")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send(
            "c",
            text_message_content("late addressed promotion"),
            sender_label="Chief",
            sender_message_id="late-promoted-id",
            sender=chief,
            recipient=recipient,
        )
        selected = (await harness.system.held_prompts("c"))[0]
        backend = harness.backend("c")
        backend.steer_outcome = steer_outcome
        backend.steer_has_begun = asyncio.Event()
        backend.steers_wait_for_release = asyncio.Event()

        steering = asyncio.create_task(
            harness.system.promote_held_prompt(
                "c", selected.held_prompt_id, HeldPromptPromotionMode.steer
            )
        )
        await backend.steer_has_begun.wait()
        await harness.complete_turn("c")
        await harness.system.send(
            "c",
            text_message_content("replacement"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
        )
        replacement = await harness.system.active_turn_reference("c")
        assert replacement is not None
        await harness.system.record_explicit_reply(replacement, OWNER_PRINCIPAL)

        backend.steers_wait_for_release.set()
        assert await steering == expected_fate
        await harness.complete_turn("c")

        payloads = [event.payload for event in await harness.events("c")]
        assert any(
            isinstance(payload, recorded_payload_type)
            and payload.sender_message_id == "late-promoted-id"
            and payload.sender == chief
            and payload.recipient == recipient
            for payload in payloads
        )
        assert not any(
            isinstance(payload, ExplicitReplyMissingEventPayload) for payload in payloads
        )
        assert backend.steer_tokens == [TurnToken("c", 1)]

    _run(exercise)


def test_each_held_queue_mutation_publishes_an_empty_live_frame(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        with harness.watch("c") as watching:
            await harness.system.send("c", text_message_content("held"), sender_label="owner")
            assert isinstance(await watching.next_item(), HeldPromptsChangedFrame)
            held = (await harness.system.held_prompts("c"))[0]
            await harness.system.discard_held_prompt("c", held.held_prompt_id)
            assert isinstance(await watching.next_item(), HeldPromptsChangedFrame)

    _run(exercise)
