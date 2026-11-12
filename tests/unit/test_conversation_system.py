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
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Coroutine, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.conversation_test_loop import conversation_test_loop, run_conversation_test

import planner.conversation.storage as conversation_storage
import planner.conversation.system as conversation_system
from planner.conversation.backends.contracts import (
    AgentMessageCompleted,
    BackendChildFactory,
    BackendEventSink,
    BackendPermissionAsk,
    BackendPromptAccepted,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerOutcome,
    BackendSteerRefused,
    BackendSteerUncertain,
    BackendStreamEnded,
    BackendUserInputRequest,
    BackgroundTaskEnded,
    BackgroundTaskStarted,
    ContextCompacted,
    NeedsRebind,
    PermissionAnswerWriteFailed,
    PermissionAskRaised,
    PromptWriteRefused,
    SessionLoadFailed,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
    UserInputAnswerWriteFailed,
    UserInputRequested,
    VendorSessionCursorRebound,
)
from planner.conversation.contracts import (
    AnswerFate,
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
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
    AgentMessageEventPayload,
    ConversationEventKind,
    ConversationTurnEnding,
    MessageToOwnerEventPayload,
    ModelThinkingFrame,
    PermissionAskedEventPayload,
    PermissionAskOption,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    ToolCallStartedEventPayload,
    TurnEndedEventPayload,
    UserInputAnswer,
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
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import (
    ConversationRecord,
    ConversationStore,
    StoredConversationEvent,
)
from planner.conversation.system import (
    SqliteProcessConversationSystem,
)
from planner.core.contracts import (
    CHIEF_PRINCIPAL,
    OWNER_PRINCIPAL,
    Principal,
    PrincipalKind,
)
from planner.core.db import connect, create_schema
from planner.worker_types.store import read_definition, workflow_from_preset, workflow_to_json

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
    sender_message_counts: list[int] = field(default_factory=list)
    composed_content_delivered: bool = True

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
            await self._sink.report(
                None, VendorSessionCursorRebound(vendor_session_cursor=VENDOR_SESSION_CURSOR)
            )

    async def write_prompt(
        self,
        turn_token: TurnToken,
        content: MessageContent,
        *,
        sender_label: str,
        sender_content: MessageContent,
        sender_message_count: int = 1,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool = False,
        reasoning_effort_is_set: bool = False,
    ) -> BackendPromptAccepted:
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
            raise PromptWriteRefused(self._backend.conversation_id)
        if self._backend.write_failures_remaining:
            self._backend.write_failures_remaining -= 1
            raise PromptWriteRefused(self._backend.conversation_id)
        if model_change is not None:
            self._backend.model = model_change
        if reasoning_effort_change is not None:
            self._backend.reasoning_effort = reasoning_effort_change
        self._backend.writes.append(
            _FakeBackendWrite(content=content, sender_content=sender_content)
        )
        self._backend.sender_message_counts.append(sender_message_count)
        self._backend.automatic_compaction_writes.append(automatic_compaction)
        self._backend.lifecycle_events.append(f"write:{message_content_text(content)}")
        self._backend.live_turn_token = turn_token
        if self._backend.ends_the_turn_while_writing:
            self._backend.ends_the_turn_while_writing = False
            await self._sink.report(
                turn_token,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            self._backend.live_turn_token = None
            # Hand the loop back enough times that the ending is certainly picked up while
            # this send is still on its way, which is the race the barrier is for.
            for _ in range(_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP):
                await asyncio.sleep(0)
        return BackendPromptAccepted(
            composed_content_delivered=self._backend.composed_content_delivered
        )

    async def steer(
        self,
        turn_token: TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent | None = None,
        sender_label: str,
    ) -> BackendSteerOutcome:
        del sender_content
        del sender_label
        if self._backend.steer_has_begun is not None:
            self._backend.steer_has_begun.set()
        if self._backend.steers_wait_for_release is not None:
            await self._backend.steers_wait_for_release.wait()
        if self._backend.write_fails:
            raise PromptWriteRefused(self._backend.conversation_id)
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
            await self._sink.report(
                token,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            # A real cancel waits on its wire, which gives the queue time to work that
            # report through before the core comes back to record its own ending. Without
            # this the two never actually race and the report always loses by accident.
            for _ in range(_SCHEDULING_TURNS_TO_LET_THE_QUEUE_CATCH_UP):
                await asyncio.sleep(0)

    async def answer_permission_ask(
        self, ask_id: str, option_id: str, **kwargs: object
    ) -> AnswerFate:
        if self._backend.permission_answer_write_fails:
            raise PermissionAnswerWriteFailed(ask_id)
        self._backend.permission_answers[ask_id] = option_id
        return AnswerFate.applied

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...], **kwargs: object
    ) -> AnswerFate:
        if self._backend.user_input_answer_write_fails:
            raise UserInputAnswerWriteFailed(request_id)
        self._backend.user_input_answers[request_id] = answers
        return AnswerFate.applied

    @property
    def stop_outside_lock(self) -> bool:
        return False

    async def withdraw_delivery(self) -> bool:
        return True

    async def detach(self) -> None:
        await self.stop()

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
        factory_wrapper: Callable[[BackendChildFactory], BackendChildFactory] | None = None,
        home_server_url: str | None = None,
    ) -> None:
        self.db_path = db_path
        self.clock = _FakeMonotonicClock() if clock is None else clock
        self.store = ConversationStore(
            str(db_path), integer_now=lambda: int(self.clock.now), home_server_url=home_server_url
        )
        self.backends: dict[str, _FakeBackend] = {}
        self.spawned_conversation_ids: list[str] = []
        self.resolved_starts: list[ResolvedConversationStart] = []
        self.live_tail = ConversationLiveTail()
        self.message_files = ConversationMessageFiles(str(db_path))
        child_factory = (
            self._make_child if factory_wrapper is None else factory_wrapper(self._make_child)
        )
        self.system = SqliteProcessConversationSystem(
            store=self.store,
            message_files=self.message_files,
            backend_child_factories={
                backend_key: child_factory for backend_key in ConversationBackendKey
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
        self.resolved_starts.append(resolved_start)
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
        await backend.sink.report(
            token,
            TurnEnded(
                ending=ending, error_summary=error_summary, standard_error_tail=standard_error_tail
            ),
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
            conversation_id,
            ConversationTurnEnding.failed,
            error_summary,
            standard_error_tail,
        )

    async def raise_permission_ask(self, conversation_id: str) -> str:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        backend.permission_asks_raised += 1
        ask_id = f"ask-{backend.permission_asks_raised}"
        await backend.sink.report(
            token,
            PermissionAskRaised(
                ask=BackendPermissionAsk(
                    ask_id=ask_id,
                    title="Run a command?",
                    detail="ls",
                    options=(
                        PermissionAskOption(
                            option_id="allow-once",
                            label="Approve once",
                            option_kind="allow",
                        ),
                        PermissionAskOption(
                            option_id="deny", label="Decline", option_kind="reject"
                        ),
                    ),
                )
            ),
        )
        await self.settle()
        return next(
            event.payload.ask_id
            for event in reversed(await self.events(conversation_id))
            if isinstance(event.payload, PermissionAskedEventPayload)
        )

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
        await backend.sink.report(token, UserInputRequested(request=request))
        await self.settle()
        public = next(
            event.payload
            for event in reversed(await self.events(conversation_id))
            if isinstance(event.payload, UserInputRequestedEventPayload)
        )
        return BackendUserInputRequest(request_id=public.request_id, questions=public.questions)

    async def agent_message(self, conversation_id: str, content: MessageContent) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.report(token, AgentMessageCompleted(content=content))
        await self.settle()

    async def confirm_compaction(self, conversation_id: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.report(token, ContextCompacted())
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
    with conversation_test_loop() as runner:
        try:
            yield built
        finally:
            runner.run(built.system.shutdown())


def _run(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    """Every test drives the system from one event loop, as a real caller would.

    The time limit is a wedge detector: nothing here does real work, so anything near it
    is a system that has stopped rather than one that is slow.
    """
    run_conversation_test(exercise)


def _retry_failed_conversation_writes_quickly(
    monkeypatch: pytest.MonkeyPatch, *, first: float = 0.01, maximum: float = 0.05
) -> None:
    """Shrink the store's own retry delay for a failing write, so a test waits briefly."""
    monkeypatch.setattr(
        conversation_storage, "CONVERSATION_WRITE_FAILURE_FIRST_RETRY_SECONDS", first
    )
    monkeypatch.setattr(
        conversation_storage, "CONVERSATION_WRITE_FAILURE_MAX_RETRY_SECONDS", maximum
    )


def _link_test_ticket_to_conversation(
    db_path: Path, conversation_id: str, *, ticket_id: str = "t_failed"
) -> None:
    conn = connect(str(db_path))
    try:
        with conn:
            conn.execute(
                "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,"
                "ceiling,conversation_id,field_values,created_at,updated_at,"
                "ceiling_holder,workflow_json) "
                "VALUES (?,?,?,'codex','needs_implementation','needs_implementation',"
                "?,'{}',1,1,?,?)",
                (
                    ticket_id,
                    "Failed Ticket",
                    "coding",
                    conversation_id,
                    json.dumps({"kind": "chief", "id": "chief"}),
                    workflow_to_json(workflow_from_preset(read_definition(conn, "coding"))),
                ),
            )
    finally:
        conn.close()


def _store_manager_notice_batch(
    db_path: Path, sender_message_id: str, *, conversation_id: str = "c"
) -> None:
    conn = connect(str(db_path))
    try:
        with conn:
            conn.execute(
                "INSERT INTO manager_wake_batches("
                "manager_id,sender_message_id,message,status,conversation_id,"
                "created_at,updated_at,target_kind,target_id) "
                "VALUES (NULL,?,?,'offering',?,1,1,'chief','chief')",
                (sender_message_id, "review failure", conversation_id),
            )
    finally:
        conn.close()


def _turn_failure_notice_count(db_path: Path) -> int:
    conn = connect(str(db_path))
    try:
        return int(
            conn.execute(
                "SELECT count(*) FROM manager_wakes WHERE source_kind='turn_failure'"
            ).fetchone()[0]
        )
    finally:
        conn.close()


async def _start(
    harness: _Harness,
    conversation_id: str = "c",
    *,
    backend_key: ConversationBackendKey = ConversationBackendKey.hermes,
    model: str = "a-model",
    reasoning_effort: str | None = None,
    role_materials: ConversationRoleMaterials | None = None,
    workspace_folder: Path = Path("/tmp/workspace"),
) -> None:
    await harness.system.start_conversation(
        ConversationStartRequest(
            conversation_id=conversation_id,
            backend_key=backend_key,
            model=model,
            reasoning_effort=reasoning_effort,
            role_materials=role_materials,
            workspace_folder=workspace_folder,
        )
    )


def test_disabled_saved_model_refuses_dispatch_until_reenabled(harness: _Harness) -> None:
    from planner.conversation.backend_state import BackendStateStore

    async def exercise() -> None:
        backend_key = ConversationBackendKey.codex
        await _start(harness, backend_key=backend_key, model="exact-provider-model")
        policy = BackendStateStore(str(harness.db_path))
        policy.write_model_enablement(backend_key, "exact-provider-model", False)
        refused = await harness.system.send(
            "c", text_message_content("blocked"), sender_label="Owner"
        )
        assert isinstance(refused, PromptDeliveryRefused)
        assert refused.refusal_reason is PromptDeliveryRefusalReason.model_resolution_failed
        rows = await harness.store.read_events_after("c", 0)
        refused_rows = [row for row in rows if row.kind.value == "prompt_delivery_refused"]
        assert len(refused_rows) == 1
        assert "disabled" in str(refused_rows[0].payload)
        assert harness.backend("c").writes == []
        policy.write_model_enablement(backend_key, "exact-provider-model", True)
        sent = await harness.system.send("c", text_message_content("allowed"), sender_label="Owner")
        assert isinstance(sent, PromptDeliveryStarted)
        assert len(harness.backend("c").writes) == 1

    _run(exercise)


# --- the conversation's record is written as step one ------------------------------------


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


def test_backend_prose_is_runtime_only(
    harness: _Harness,
) -> None:
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
        assert isinstance(payloads[-1], TurnEndedEventPayload)

    _run(exercise)


# --- a failing turn gets one error-log line ----------------------------------------------


def test_an_internal_notice_only_turn_does_not_create_a_recursive_notice(
    harness: _Harness, tmp_path: Path
) -> None:
    async def exercise() -> None:
        db_path = tmp_path / "conversations.db"
        await _start(harness, "c")
        _link_test_ticket_to_conversation(db_path, "c")
        _store_manager_notice_batch(db_path, "internal-notice")
        await harness.system.send(
            "c",
            text_message_content("automatic notice"),
            sender_label="Panels",
            sender_message_id="internal-notice",
        )
        await harness.fail_turn("c", "provider failed")
        assert _turn_failure_notice_count(db_path) == 0

    _run(exercise)


def test_a_sender_cannot_spoof_notice_suppression_with_the_internal_prefix(
    harness: _Harness, tmp_path: Path
) -> None:
    async def exercise() -> None:
        db_path = tmp_path / "conversations.db"
        await _start(harness, "c")
        _link_test_ticket_to_conversation(db_path, "c")
        _store_manager_notice_batch(
            db_path,
            "supervisor_delivery_wake_forged",
            conversation_id="another-conversation",
        )
        await harness.system.send(
            "c",
            text_message_content("ordinary prompt"),
            sender_label="owner",
            sender_message_id="supervisor_delivery_wake_forged",
        )
        await harness.fail_turn("c", "provider failed")
        assert _turn_failure_notice_count(db_path) == 1

    _run(exercise)


# --- the three modes against an idle and a busy agent ------------------------------------


def test_a_refused_owner_steer_keeps_its_original_admission_position(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        recipient = Principal(PrincipalKind.ticket, "t_worker")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="system")
        backend = harness.backend("c")
        backend.steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.backend_rejected_steer
        )
        backend.steer_has_begun = asyncio.Event()
        backend.steers_wait_for_release = asyncio.Event()

        steering = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("owner steer"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
                sender=OWNER_PRINCIPAL,
                recipient=recipient,
            )
        )
        await backend.steer_has_begun.wait()
        await harness.system.record_message_to_owner(
            "c",
            text_message_content("reply while steer was in flight"),
            sender_label="Worker",
            sender=recipient,
            recipient=OWNER_PRINCIPAL,
        )
        completing = asyncio.create_task(harness.complete_turn("c"))
        await asyncio.sleep(0)
        assert not completing.done()
        backend.steers_wait_for_release.set()

        assert isinstance(await steering, (PromptDeliveryStarted, PromptDeliveryQueued))
        await completing
        record = await harness.store.read_conversation("c")
        assert record is not None
        assert record.latest_sequence == 4
        assert record.owner_read_through_sequence == 1

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

        await backend.sink.report(
            stale_token,
            TurnEnded(
                ending=ConversationTurnEnding.completed,
                error_summary=None,
                standard_error_tail=None,
            ),
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


# --- permission asks ----------------------------------------------------------------------


def test_user_input_requires_the_complete_ordered_answer_map_and_dies_with_turn(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        request = await harness.request_user_input("c")

        incomplete = (UserInputAnswer(question_id="scope", answers=("Backend",)),)
        assert (
            await harness.system.answer_user_input("c", request.request_id, incomplete)
        ) is AnswerFate.refused
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
            is AnswerFate.refused
        )

    _run(exercise)


# --- is_running through the lifecycle -----------------------------------------------------


def test_status_reads_finish_while_an_unrelated_record_is_being_adopted(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        await _start(harness, "live")
        await harness.system.send("live", text_message_content("work"), sender_label="owner")
        await harness.raise_permission_ask("live")
        await harness.request_user_input("live")
        live_record = await harness.store.read_conversation("live")
        assert live_record is not None
        # Persist cold records without adding them to the runtime registry.
        for conversation_id in ("adopting", "cold"):
            await harness.store.create_conversation(
                replace(live_record.resolved_start(), conversation_id=conversation_id)
            )

        entered, release = asyncio.Event(), asyncio.Event()
        read_conversation = harness.store.read_conversation

        async def gated_read(conversation_id: str) -> ConversationRecord | None:
            if conversation_id == "adopting":
                entered.set()
                await release.wait()
            return await read_conversation(conversation_id)

        monkeypatch.setattr(harness.store, "read_conversation", gated_read)
        adoption = asyncio.create_task(harness.system._conversation_state("adopting"))
        try:
            await asyncio.wait_for(entered.wait(), 5)

            async def read_statuses() -> None:
                for conversation_id in ("cold", "missing", "live"):
                    expected = conversation_id == "live"
                    assert await harness.system.is_running(conversation_id) is expected
                    assert (
                        await harness.system.has_pending_permission_ask(conversation_id) is expected
                    )
                    assert await harness.system.has_pending_user_input(conversation_id) is expected

            await asyncio.wait_for(read_statuses(), 5)
            assert not release.is_set()
            assert not adoption.done()
            assert set(harness.system._conversations) == {"live"}
        finally:
            release.set()
            await adoption

    _run(exercise)


# --- saying the model is thinking ----------------------------------------------------------


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


def test_local_possible_emission_stays_uncertain_and_duplicate_never_writes_again(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local failure after possible emission cannot use the prewrite-refusal fallback."""
    from planner.conversation.backends.contracts import PromptWriteFailed
    from planner.conversation.events import PromptDeliveryRefusedEventPayload
    from planner.conversation.lifecycle import LifecycleStatus

    calls = 0

    async def possible_emission(self: _FakeBackendChild, *args: Any, **kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        raise PromptWriteFailed("acknowledgment lost after possible transmission")

    monkeypatch.setattr(_FakeBackendChild, "write_prompt", possible_emission)

    async def exercise() -> None:
        await _start(harness, "c")
        for _ in range(2):
            assert (
                await harness.system.send(
                    "c",
                    text_message_content("send once"),
                    sender_label="owner",
                    sender_message_id="one-uncertain-message",
                )
                == PromptDeliveryUncertain()
            )
        assert calls == 1
        assert any(
            fact.status is LifecycleStatus.unknown
            for fact in await harness.store.lifecycle_facts("c")
        )
        assert not any(
            isinstance(event.payload, PromptDeliveryRefusedEventPayload)
            for event in await harness.events("c")
        )

    _run(exercise)


def test_a_refused_first_delivery_leaves_the_role_text_for_the_next_one(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The role rides the first prompt that is actually delivered, not the first attempt."""

    from planner.conversation.backends.contracts import BackendPromptRejected

    original_write = _FakeBackendChild.write_prompt

    async def known_rejection(
        self: _FakeBackendChild, *args: Any, **kwargs: Any
    ) -> BackendPromptAccepted:
        if self._backend.write_fails:
            raise BackendPromptRejected("provider proved non-admission")
        return await original_write(self, *args, **kwargs)

    monkeypatch.setattr(_FakeBackendChild, "write_prompt", known_rejection)

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


def test_boundary_duplicate_waits_for_compaction_and_runs_once(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        content = text_message_content("at the boundary")
        first = await harness.system.send(
            "c", content, sender_label="owner", sender_message_id="same-id"
        )
        duplicate = await harness.system.send(
            "c", content, sender_label="owner", sender_message_id="same-id"
        )
        assert isinstance(first, PromptDeliveryQueued)
        assert isinstance(duplicate, PromptDeliveryQueued)
        assert harness.backend("c").written_texts() == ("first", "/compact")
        await harness.confirm_compaction("c")
        await harness.complete_turn("c")
        assert harness.backend("c").written_texts().count("at the boundary") == 1

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
        await harness.settle()
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
        assert released.endswith("owner message\n\nTicket t_worker:\nworker opener")
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


def test_accepted_compaction_recording_failure_retains_child_and_retries_storage_only(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _retry_failed_conversation_writes_quickly(monkeypatch)

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.clock.advance(50 * 60)
        real_append = harness.store.append_delivered_prompt
        failed = asyncio.Event()
        unavailable = True
        recorded_compactions = 0

        async def fail_compaction_record(conversation_id: str, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal recorded_compactions
            is_compaction = message_content_text(kwargs["prompt"].content) == "/compact"
            if unavailable and is_compaction:
                failed.set()
                raise sqlite3.OperationalError("record unavailable")
            recorded = await real_append(conversation_id, **kwargs)
            recorded_compactions += is_compaction
            return recorded

        harness.store.append_delivered_prompt = fail_compaction_record  # type: ignore[method-assign]
        sending = asyncio.create_task(
            harness.system.send("c", text_message_content("must stay held"), sender_label="owner")
        )
        await failed.wait()
        await asyncio.sleep(0)
        backend = harness.backend("c")
        assert backend.written_texts() == ("first", "/compact")
        assert backend.live_children == 1
        assert backend.cancellations == 0
        assert not sending.done()
        assert recorded_compactions == 0
        assert harness.system._conversations["c"].reserved_turn is not None
        # The cause clears; the store's own retry records the compaction, and only storage
        # is retried: the child is never asked for /compact again.
        unavailable = False
        assert isinstance(await sending, PromptDeliveryQueued)
        await harness.complete_turn("c")
        assert backend.written_texts() == ("first", "/compact", "must stay held")
        assert backend.cancellations == 0
        assert recorded_compactions == 1

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
            harness.system.send("c", content, sender_label="owner", sender_message_id="held-race")
        )
        await asyncio.sleep(0)
        assert not duplicate.done()
        backend.writes_wait_for_release.set()
        await ending
        assert await duplicate == PromptDeliveryStarted()
        assert backend.written_texts().count("held once") == 1

    _run(exercise)


def test_after_a_restart_nothing_is_running_and_the_next_message_resumes(
    harness: _Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No re-attach on boot: the record is all there and the session is picked up lazily."""

    async def exercise() -> None:
        qualified_model = "openai-codex:gpt-5.6-sol"
        original_workspace = tmp_path / "original-workspace"
        original_workspace.mkdir()
        await _start(harness, "c", model=qualified_model, workspace_folder=original_workspace)
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
            reads: list[str] = []
            read_conversation = restarted.store.read_conversation

            async def count_read(conversation_id: str) -> ConversationRecord | None:
                reads.append(conversation_id)
                return await read_conversation(conversation_id)

            with monkeypatch.context() as patch:
                patch.setattr(restarted.store, "read_conversation", count_read)
                for conversation_id in ("c", "missing"):
                    assert await restarted.system.is_running(conversation_id) is False
                    assert (
                        await restarted.system.has_pending_permission_ask(conversation_id) is False
                    )
                    assert await restarted.system.has_pending_user_input(conversation_id) is False
                assert reads == []
                assert restarted.system._conversations == {}

            from planner.runtime.workspaces import register_workspace_root

            changed_default = tmp_path / "changed-default"
            changed_default.mkdir()
            with register_workspace_root(str(restarted.db_path), changed_default):
                fate = await restarted.system.send(
                    "c",
                    text_message_content("after the restart"),
                    sender_label="owner",
                )

            assert fate == PromptDeliveryStarted()
            assert restarted.backend("c").started_from_cursor == VENDOR_SESSION_CURSOR
            assert restarted.backend("c").model == qualified_model
            assert restarted.resolved_starts[-1].workspace_folder == original_workspace
            assert await restarted.system.is_running("c") is True
            assert [str(event.kind) for event in await restarted.events("c")] == [
                "prompt",
                "prompt",
            ]
        finally:
            await restarted.system.shutdown()

    _run(exercise)


# --- killing a conversation's activity ----------------------------------------------------


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
        backend.reports_its_ending_during_a_cancel = True

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
        # A send admitted while cancellation releases the lock can queue. Either
        # delivery fate must lead to the message reaching the successor child.
        assert isinstance(await sending, (PromptDeliveryStarted, PromptDeliveryQueued))
        await harness.settle()

        assert await harness.system.is_running("c") is True
        assert backend.written_texts() == ("incumbent", "sent during the kill")
        assert await harness.recorded_endings("c") == (ConversationTurnEnding.interrupted,)

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
            (
                event.payload.sender_message_id,
                message_content_text(event.payload.content),
            )
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


# --- an ending is one ending, and a cancel that failed is not a cancel --------------------


# --- one agent per conversation, even mid-sweep -------------------------------------------


# --- live frames belong to a turn -----------------------------------------------------------


# --- starting a conversation a message is already using -------------------------------------


# --- a turn that ended and could not be written down -----------------------------------------


def test_vps_only_write_failure_retries_in_order_with_growing_delay_until_it_succeeds(
    harness: _Harness,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write the VPS holds the only copy of is retried by itself until it lands.

    Each failure is logged with the delay before the next try, which doubles up to a cap.
    Meanwhile the turn ending stays unrecorded, the turn stays live, and backend news
    behind it waits its turn; once the cause clears the ending is recorded exactly once,
    and nothing has to release it.
    """
    _retry_failed_conversation_writes_quickly(monkeypatch, first=0.01, maximum=0.02)
    caplog.set_level(logging.ERROR, logger="planner.conversation")

    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        backend = harness.backend("c")
        turn = backend.live_turn_token
        sink = backend.sink
        assert turn is not None and sink is not None
        # The same news while the turn is live is recorded, so dropping it later is a choice.
        await sink.report(
            turn, ToolCallStarted(tool_call_id="live", title="Read a file", tool_kind="read")
        )
        await harness.settle()
        real_commit = harness.store.commit_backend_event
        third_failure = asyncio.Event()
        failures = 0
        available = False

        async def unavailable(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal failures
            if available:
                return await real_commit(*args, **kwargs)
            failures += 1
            if failures == 3:
                third_failure.set()
            raise sqlite3.OperationalError("disk unavailable")

        harness.store.commit_backend_event = unavailable  # type: ignore[method-assign]
        completing = asyncio.create_task(harness.fail_turn("c", error_summary="the model refused"))
        await third_failure.wait()
        # More news for the same turn, reported behind its ending through the same sink.
        await sink.report(
            turn, ToolCallStarted(tool_call_id="late", title="Read a file", tool_kind="read")
        )
        assert not completing.done()
        assert await harness.recorded_endings("c") == ()
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.tool_call_started,
        )
        state = harness.system._conversations["c"]
        assert state.running_turn is not None and not state.running_turn.ended
        waiting_for_record = asyncio.create_task(state.backend_event_queue.join())
        await asyncio.sleep(0)
        assert not waiting_for_record.done()
        waiting_for_record.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting_for_record

        available = True
        await completing
        assert await harness.recorded_endings("c") == (ConversationTurnEnding.failed,)
        # The late news waited behind the ending, then was dropped: its turn had ended.
        assert await harness.recorded_kinds("c") == (
            ConversationEventKind.prompt,
            ConversationEventKind.tool_call_started,
            ConversationEventKind.turn_ended,
        )
        tool_calls = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, ToolCallStartedEventPayload)
        ]
        assert [tool_call.tool_call_id for tool_call in tool_calls] == ["live"]
        assert await harness.system.is_running("c") is False

        logged = [
            record
            for record in caplog.records
            if record.name == "planner.conversation"
            and "save failed (failure" in record.getMessage()
        ]
        assert len(logged) == failures >= 3
        assert all(record.levelno == logging.ERROR for record in logged)
        assert all("disk unavailable" in record.getMessage() for record in logged)
        parsed = [
            re.search(r"\(failure (\d+)\); retrying in ([0-9.]+) s", record.getMessage())
            for record in logged
        ]
        assert all(match is not None for match in parsed)
        numbers = [int(match.group(1)) for match in parsed if match is not None]
        delays = [float(match.group(2)) for match in parsed if match is not None]
        assert numbers == list(range(1, failures + 1))
        assert delays == [0.01] + [0.02] * (failures - 1)

    _run(exercise)


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


def test_promoted_send_now_claims_one_message_and_preserves_fifo(
    harness: _Harness,
) -> None:
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
        assert [message_content_text(item.content) for item in waiting] == [
            "first",
            "last",
        ]
        assert (
            await harness.system.promote_held_prompt(
                "c", held[1].held_prompt_id, HeldPromptPromotionMode.send_now
            )
            is None
        )

        # Messages without explicit changes use the model current at dispatch.
        # Their FIFO order survives promotion, and they may share the next turn.
        await harness.complete_turn("c")
        assert harness.backend("c").written_texts() == (
            "incumbent",
            "selected",
            "first\n\nowner:\nlast",
        )
        assert harness.backend("c").model == "selected-model"

    _run(exercise)


def test_two_promotions_of_one_held_id_have_exactly_one_winner(
    harness: _Harness,
) -> None:
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


@pytest.mark.parametrize(
    ("steer_outcome", "expected_fate"),
    [
        (BackendSteerAccepted(), PromptDeliveryInjected()),
        (BackendSteerUncertain(), PromptDeliveryUncertain()),
    ],
)
def test_an_owner_prompt_promoted_to_steer_credits_only_its_queue_admission_position(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    steer_outcome: BackendSteerOutcome,
    expected_fate: PromptDeliveryInjected | PromptDeliveryUncertain,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        recipient = Principal(PrincipalKind.ticket, "t_worker")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="system")
        await harness.system.send(
            "c",
            text_message_content("owner prompt"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=recipient,
        )
        selected = (await harness.system.held_prompts("c"))[0]
        await harness.agent_message("c", text_message_content("later unseen reply"))
        harness.backend("c").steer_outcome = steer_outcome

        assert (
            await harness.system.promote_held_prompt(
                "c", selected.held_prompt_id, HeldPromptPromotionMode.steer
            )
            == expected_fate
        )

        record = await harness.store.read_conversation("c")
        assert record is not None
        assert record.latest_sequence > 1
        assert record.owner_read_through_sequence == 1

    _run(exercise)


@pytest.mark.parametrize(
    ("steer_outcome", "expected_fate"),
    [
        (BackendSteerAccepted(), PromptDeliveryInjected()),
        (BackendSteerUncertain(), PromptDeliveryUncertain()),
    ],
)
def test_a_promoted_steer_joins_the_target_turn_while_it_remains_active(
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
        assert "addressed promotion" in harness.backend("c").writes[-1].text

    _run(exercise)


def test_a_promoted_command_goes_back_to_the_line_instead_of_being_thrown_away(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Promoting a held command into a running turn is the right gesture at the wrong
    moment. The message keeps its place in the line and runs as its own turn."""

    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        await harness.system.send("c", text_message_content("/compact"), sender_label="owner")
        selected = (await harness.system.held_prompts("c"))[0]
        harness.backend("c").steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.command_cannot_join_running_turn
        )

        fate = await harness.system.promote_held_prompt(
            "c", selected.held_prompt_id, HeldPromptPromotionMode.steer
        )

        assert fate == PromptDeliveryQueued(queue_position=1)
        held = await harness.system.held_prompts("c")
        assert [message_content_text(message.content) for message in held] == ["/compact"]
        assert [message.queue_reason for message in held] == [
            PromptQueueReason.command_needs_its_own_turn
        ]

    _run(exercise)


@pytest.mark.parametrize("site", ["direct", "promoted"])
def test_a_steer_the_mac_did_not_take_becomes_the_next_message(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, site: str
) -> None:
    """A steer the Mac was away for, or did not take in time, is not thrown away. It
    waits in the line and opens the next turn, whichever way it was sent."""

    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        backend.steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.remote_steer_not_delivered
        )
        fate: object
        if site == "direct":
            fate = await harness.system.send(
                "c",
                text_message_content("late steer"),
                sender_label="owner",
                mode=PromptDeliveryMode.steer,
            )
        else:
            await harness.system.send("c", text_message_content("late steer"), sender_label="owner")
            selected = (await harness.system.held_prompts("c"))[0]
            fate = await harness.system.promote_held_prompt(
                "c", selected.held_prompt_id, HeldPromptPromotionMode.steer
            )

        assert fate == PromptDeliveryQueued(queue_position=1)
        held = await harness.system.held_prompts("c")
        assert [message.queue_reason for message in held] == [PromptQueueReason.steer_refused]
        assert ConversationEventKind.prompt_delivery_refused not in await harness.recorded_kinds(
            "c"
        )
        await harness.complete_turn("c")
        assert backend.writes[-1].text == "late steer"
        assert not backend.writes[-1].steered
        assert await harness.system.held_prompts("c") == ()

    _run(exercise)


@pytest.mark.parametrize("never_arrived", [True, False])
def test_an_uncertain_steer_that_later_proves_it_never_arrived_becomes_the_next_message(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, never_arrived: bool
) -> None:
    """Risk: a Mac steer recorded uncertain whose row later proves it never reached its
    turn (refused because the turn ended, or withdrawn when the Mac process was replaced)
    is lost, or goes behind a message sent after it, because its turn ended first. It
    goes to the line as the next message, ahead of what was sent after it, and until it
    is known nothing sent after it starts. One that did arrive is not sent again."""

    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="owner")
        backend = harness.backend("c")
        verdict: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        backend.steer_outcome = BackendSteerUncertain(None, not_delivered=verdict)
        fate = await harness.system.send(
            "c",
            text_message_content("late steer"),
            sender_label="owner",
            mode=PromptDeliveryMode.steer,
        )
        assert fate == PromptDeliveryUncertain()
        await harness.system.send("c", text_message_content("next"), sender_label="owner")
        # The turn ends before the steer's row says anything: "next" waits for it.
        await harness.complete_turn("c")
        assert [write.text for write in backend.writes if not write.steered] == ["incumbent"]
        verdict.set_result(never_arrived)
        await harness.settle()
        # The steer goes first; the two may share the next turn.
        assert [write.text for write in backend.writes if not write.steered][1:] == [
            "late steer\n\nowner:\nnext" if never_arrived else "next"
        ]
        assert await harness.system.held_prompts("c") == ()

    _run(exercise)


def test_a_steer_left_uncertain_by_the_previous_process_goes_as_the_next_message_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: the VPS restarts while a Mac steer is recorded uncertain. The wait for its
    row lived in memory, so a row that then proves the steer never arrived loses it.
    Gap: the case above keeps one process. A restarted core watches it again from its
    uncertain row, delivers it once, and a later restart does not send it again."""
    monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
    db_path = tmp_path / "conversations.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()

    async def exercise() -> None:
        first = _Harness(db_path)
        await _start(first, "c")
        await first.system.send("c", text_message_content("incumbent"), sender_label="owner")
        first.backend("c").steer_outcome = BackendSteerUncertain(
            None, not_delivered=asyncio.get_running_loop().create_future()
        )
        await first.system.send(
            "c",
            text_message_content("late steer"),
            sender_label="owner",
            mode=PromptDeliveryMode.steer,
            sender_message_id="steer-1",
        )
        await first.complete_turn("c")
        (uncertain,) = [
            event.sequence
            for event in await first.events("c")
            if event.kind is ConversationEventKind.prompt_delivery_uncertain
        ]
        await first.system.shutdown()

        for _ in range(2):  # A second restart finds it delivered.
            restarted = _Harness(db_path)
            try:
                arrived: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
                arrived.set_result(True)
                await restarted.system.await_late_steer("c", uncertain, arrived)
                await restarted.settle()
            finally:
                await restarted.system.shutdown()
        prompts = [
            message_content_text(event.payload.content)
            for event in await _Harness(db_path).events("c")
            if isinstance(event.payload, PromptEventPayload)
        ]
        assert prompts == ["incumbent", "late steer"]

    _run(exercise)


def test_caller_cancellation_after_acceptance_preserves_receipt_and_duplicate_identity(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        entered = asyncio.Event()
        release = asyncio.Event()
        original = harness.store.append_delivered_prompt
        receipt_calls = 0

        async def blocked(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal receipt_calls
            receipt_calls += 1
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        harness.store.append_delivered_prompt = blocked  # type: ignore[method-assign]
        sender = Principal(PrincipalKind.manager, "sender")
        recipient = Principal(PrincipalKind.ticket, "worker")
        first = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("one accepted message"),
                sender_label="Supervisor",
                sender_message_id="same-id",
                sender=sender,
                recipient=recipient,
            )
        )
        await entered.wait()
        assert harness.backend("c").written_texts()[0] == "one accepted message"
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        duplicate = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("one accepted message"),
                sender_label="Supervisor",
                sender_message_id="same-id",
                sender=sender,
                recipient=recipient,
            )
        )
        await asyncio.sleep(0)
        assert not duplicate.done()
        assert receipt_calls == 1
        assert harness.backend("c").live_children == 1
        release.set()
        assert isinstance(await duplicate, PromptDeliveryStarted)
        assert len(harness.backend("c").writes) == 1
        await harness.complete_turn("c")
        events = await harness.store.read_events_after("c", 0)
        assert sum(isinstance(event.payload, PromptEventPayload) for event in events) == 1

    _run(exercise)


def test_active_steer_receipt_barrier_records_the_steer_before_completion(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("first"), sender_label="system")
        entered = asyncio.Event()
        release = asyncio.Event()
        original = harness.store.append_events

        async def blocked(conversation_id, payloads, **kwargs):  # type: ignore[no-untyped-def]
            if any(
                isinstance(payload, PromptEventPayload) and payload.mode is PromptDeliveryMode.steer
                for payload in payloads
            ):
                entered.set()
                await release.wait()
            return await original(conversation_id, payloads, **kwargs)

        harness.store.append_events = blocked  # type: ignore[method-assign]
        sender = Principal(PrincipalKind.manager, "supervisor")
        steering = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("joined instruction"),
                sender_label="Supervisor",
                mode=PromptDeliveryMode.steer,
                sender=sender,
                recipient=Principal(PrincipalKind.ticket, "worker"),
                sender_message_id="steer-id",
            )
        )
        await entered.wait()
        completing = asyncio.create_task(harness.complete_turn("c"))
        await asyncio.sleep(0)
        assert not completing.done()
        steering.cancel()
        with pytest.raises(asyncio.CancelledError):
            await steering
        release.set()
        await completing
        events = await harness.store.read_events_after("c", 0)
        assert [type(event.payload) for event in events] == [
            PromptEventPayload,
            PromptEventPayload,
            TurnEndedEventPayload,
        ]
        assert len(harness.backend("c").writes) == 2

    _run(exercise)


def test_failure_after_receipt_commit_finishes_turn_without_duplicate_append(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")

        def failed_publication(event):  # type: ignore[no-untyped-def]
            raise RuntimeError("tail publication unavailable")

        monkeypatch.setattr(harness.live_tail, "publish_event", failed_publication)
        assert (
            await harness.system.send(
                "c",
                text_message_content("committed"),
                sender_label="owner",
                sender_message_id="id",
            )
            == PromptDeliveryStarted()
        )
        assert (
            await harness.system.send(
                "c",
                text_message_content("committed"),
                sender_label="owner",
                sender_message_id="id",
            )
            == PromptDeliveryStarted()
        )
        assert len(harness.backend("c").writes) == 1
        assert len(await harness.store.read_events_after("c", 0)) == 1
        assert await harness.system.is_running("c") is True

    _run(exercise)


@pytest.mark.parametrize("cancel_at", ["backend", "sqlite"])
def test_cancelled_exchange_or_sqlite_call_finishes_one_owned_settlement(
    harness: _Harness,
    cancel_at: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    async def exercise() -> None:
        await _start(harness, "c")
        backend = harness.backend("c")
        sqlite_entered = threading.Event()
        sqlite_release = threading.Event()
        calls = 0
        if cancel_at == "backend":
            backend.write_has_begun = asyncio.Event()
            backend.writes_wait_for_release = asyncio.Event()
        else:
            original = harness.store._append_delivered_prompt_sync

            def blocked(*args, **kwargs):  # type: ignore[no-untyped-def]
                nonlocal calls
                calls += 1
                sqlite_entered.set()
                assert sqlite_release.wait(3)
                return original(*args, **kwargs)

            monkeypatch.setattr(harness.store, "_append_delivered_prompt_sync", blocked)
        sending = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("keep ownership"),
                sender_label="owner",
                sender_message_id="cancel-id",
            )
        )
        if cancel_at == "backend":
            assert backend.write_has_begun is not None
            await backend.write_has_begun.wait()
        else:
            assert await asyncio.to_thread(sqlite_entered.wait, 3)
        sending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sending
        duplicate = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("keep ownership"),
                sender_label="owner",
                sender_message_id="cancel-id",
            )
        )
        await asyncio.sleep(0)
        assert not duplicate.done()
        assert len(harness.system._conversations["c"].settlement_tasks) >= 1
        if cancel_at == "backend":
            assert backend.writes_wait_for_release is not None
            backend.writes_wait_for_release.set()
        else:
            sqlite_release.set()
        assert isinstance(await duplicate, PromptDeliveryStarted)
        assert len(backend.writes) == 1
        assert len(await harness.store.read_events_after("c", 0)) == 1
        if cancel_at == "sqlite":
            assert calls == 1

    _run(exercise)


def test_non_lock_failure_preserves_accepted_identity_until_its_retry_succeeds(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _retry_failed_conversation_writes_quickly(monkeypatch)

    async def exercise() -> None:
        await _start(harness, "c")
        original = harness.store.append_delivered_prompt
        entered = asyncio.Event()
        available = False
        failed_attempts = 0
        recorded_attempts = 0

        async def unavailable(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal failed_attempts, recorded_attempts
            if not available:
                failed_attempts += 1
                entered.set()
                raise OSError("record volume unavailable")
            recorded_attempts += 1
            return await original(*args, **kwargs)

        harness.store.append_delivered_prompt = unavailable  # type: ignore[method-assign]
        sending = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("accepted"),
                sender_label="owner",
                sender_message_id="id",
            )
        )
        await entered.wait()
        duplicate = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("accepted"),
                sender_label="owner",
                sender_message_id="id",
            )
        )
        await asyncio.sleep(0.02)
        assert not sending.done() and not duplicate.done()
        assert failed_attempts >= 1 and recorded_attempts == 0
        assert len(harness.backend("c").writes) == 1
        assert harness.backend("c").live_children == 1
        assert await harness.store.read_events_after("c", 0) == ()
        state = harness.system._conversations["c"]
        assert state.reserved_turn is not None
        # The cause clears; the store's own retry records the one accepted prompt.
        available = True
        assert await sending == PromptDeliveryStarted()
        assert await duplicate == PromptDeliveryStarted()
        assert recorded_attempts == 1
        assert len(harness.backend("c").writes) == 1
        assert len(await harness.store.read_events_after("c", 0)) == 1

    _run(exercise)


@pytest.mark.parametrize("delivery", ["queued", "promoted"])
def test_preflight_metadata_error_retains_held_guidance_until_its_retry_succeeds(
    harness: _Harness,
    delivery: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _retry_failed_conversation_writes_quickly(monkeypatch)

    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="system")
        await harness.system.send(
            "c",
            text_message_content("preserved human instruction"),
            sender_label="owner",
            sender_message_id="held-metadata-id",
        )
        original = harness.store.all_sender_messages_are_manager_notices
        failed = asyncio.Event()
        available = False
        failed_calls = 0

        async def unavailable(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal failed_calls
            if not available:
                failed_calls += 1
                failed.set()
                raise OSError("metadata read unavailable")
            return await original(*args, **kwargs)

        harness.store.all_sender_messages_are_manager_notices = unavailable  # type: ignore[method-assign]
        progressing: asyncio.Task[object]
        if delivery == "queued":
            progressing = asyncio.create_task(harness.complete_turn("c"))
        else:
            held = await harness.system.held_prompts("c")
            progressing = asyncio.create_task(
                harness.system.promote_held_prompt(
                    "c",
                    held[0].held_prompt_id,
                    HeldPromptPromotionMode.steer,
                )
            )
        await failed.wait()
        await asyncio.sleep(0.02)
        assert failed_calls >= 1
        assert not progressing.done()
        assert harness.backend("c").written_texts() == ("incumbent",)
        # The cause clears; the store's own retry reads the metadata and the guidance goes.
        available = True
        await progressing
        assert harness.backend("c").written_texts().count("preserved human instruction") == 1
        events = await harness.store.read_events_after("c", 0)
        assert not any(isinstance(event.payload, PromptDiscardedEventPayload) for event in events)
        outcome = await harness.store.sender_message_outcome("c", "held-metadata-id")
        assert outcome is not None and isinstance(outcome.payload, PromptEventPayload)

    _run(exercise)


def test_accepted_queued_prompt_keeps_receipt_and_completion_behind_real_writer_lock(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        harness.store._busy_timeout_ms = 10
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="system")
        await harness.system.send(
            "c",
            text_message_content("accepted held guidance"),
            sender_label="owner",
            sender_message_id="held-id",
        )
        state = harness.system._conversations["c"]
        child = state.child
        assert child is not None
        original = child.write_prompt
        release_task: asyncio.Task[None] | None = None
        backend = harness.backend("c")

        async def accept_then_lock(*args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal release_task
            accepted = await original(*args, **kwargs)
            writer = connect(harness.store._db_path)
            writer.execute("BEGIN IMMEDIATE")
            token = backend.live_turn_token
            assert token is not None and backend.sink is not None
            await backend.sink.report(
                token,
                TurnEnded(
                    ending=ConversationTurnEnding.completed,
                    error_summary=None,
                    standard_error_tail=None,
                ),
            )
            backend.live_turn_token = None

            async def release_independently() -> None:
                await asyncio.sleep(0.12)
                assert state.reserved_turn is not None
                assert backend.live_children == 1
                assert backend.cancellations == 0
                # Completion is still waiting for its accepted receipt.
                assert len(await harness.recorded_endings("c")) == 1
                writer.execute("ROLLBACK")
                writer.close()

            release_task = asyncio.create_task(release_independently())
            return accepted

        monkeypatch.setattr(child, "write_prompt", accept_then_lock)
        await harness.complete_turn("c")
        assert release_task is not None
        await release_task
        events = await harness.store.read_events_after("c", 0)
        assert [type(event.payload) for event in events] == [
            PromptEventPayload,
            TurnEndedEventPayload,
            PromptEventPayload,
            TurnEndedEventPayload,
        ]
        assert backend.written_texts() == ("incumbent", "accepted held guidance")
        assert await harness.system.is_running("c") is False

    _run(exercise)


def test_stale_outcome_read_cannot_readmit_a_completed_sender_identity(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        original = harness.store.sender_message_outcome
        entered = asyncio.Event()
        release = asyncio.Event()
        queries = 0

        async def delayed_snapshot(*args):  # type: ignore[no-untyped-def]
            nonlocal queries
            queries += 1
            outcome = await original(*args)
            if queries == 1:
                entered.set()
                await release.wait()
            return outcome

        harness.store.sender_message_outcome = delayed_snapshot  # type: ignore[method-assign]
        stale = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("same instruction"),
                sender_label="owner",
                sender_message_id="race-id",
            )
        )
        await entered.wait()
        first = await harness.system.send(
            "c",
            text_message_content("same instruction"),
            sender_label="owner",
            sender_message_id="race-id",
        )
        assert first == PromptDeliveryStarted()
        release.set()
        assert await stale == PromptDeliveryStarted()
        assert len(harness.backend("c").writes) == 1
        assert len(await harness.store.read_events_after("c", 0)) == 1

    _run(exercise)


@pytest.mark.parametrize("control", ["interrupt", "send_now"])
def test_turn_replacement_waits_for_accepted_steer_receipt(
    harness: _Harness,
    control: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _: True)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("incumbent"), sender_label="system")
        child = harness.system._conversations["c"].child
        assert child is not None
        original = child.steer
        accepted = asyncio.Event()
        release = asyncio.Event()

        async def accepted_without_ack(*args, **kwargs):  # type: ignore[no-untyped-def]
            outcome = await original(*args, **kwargs)
            accepted.set()
            await release.wait()
            return outcome

        monkeypatch.setattr(child, "steer", accepted_without_ack)
        sender = Principal(PrincipalKind.manager, "supervisor")
        steering = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content("joined guidance"),
                sender_label="Supervisor",
                sender_message_id="steer-before-control",
                mode=PromptDeliveryMode.steer,
                sender=sender,
                recipient=Principal(PrincipalKind.ticket, "worker"),
            )
        )
        await accepted.wait()
        controlling: asyncio.Task[object]
        if control == "interrupt":
            controlling = asyncio.create_task(harness.system.interrupt("c"))
        else:
            controlling = asyncio.create_task(
                harness.system.send(
                    "c",
                    text_message_content("replacement"),
                    sender_label="owner",
                    mode=PromptDeliveryMode.send_now,
                )
            )
        await asyncio.sleep(0.02)
        assert not controlling.done()
        assert harness.backend("c").cancellations == 0
        release.set()
        assert isinstance(await steering, PromptDeliveryInjected)
        await controlling
        events = await harness.store.read_events_after("c", 0)
        types = [type(event.payload) for event in events]
        assert types[:3] == [
            PromptEventPayload,
            PromptEventPayload,
            TurnEndedEventPayload,
        ]
        assert harness.backend("c").cancellations == 1
        assert len(harness.backend("c").writes) == (2 if control == "interrupt" else 3)

    _run(exercise)


# These cases exercise the real durable queue and admission transaction. The fake only
# replaces the child wire; session barriers expose state changes before final admission.
def _review_notice(db_path: Path, *, conversation_id: str = "c") -> tuple[str, str, str]:
    from planner.manager_wakes import data as wake_data
    from planner.tickets import data as tickets_data
    from planner.tickets.contracts import TITLE_MAX_CHARS

    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            title="Review dispatch",
            principal=CHIEF_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
            brief="Review",
            stated_ceiling="needs_what_changes",
        )
        tickets_data.file_current_proposal(
            conn,
            ticket.id,
            body="Success",
            principal=Principal(PrincipalKind.ticket, ticket.id),
            now=2,
        )
        batch = wake_data.claim_next_batch(conn, process_token="test", now=3)
        assert batch is not None
        wake_data.record_batch_offering(
            conn,
            batch.id,
            conversation_id=conversation_id,
            process_token="test",
            now=3,
        )
        return ticket.id, batch.sender_message_id, batch.message
    finally:
        conn.close()


def _resolve_review(db_path: Path, ticket_id: str) -> None:
    from planner.tickets import data as tickets_data

    conn = connect(str(db_path))
    try:
        tickets_data.accept_proposal(
            conn,
            ticket_id,
            field="what_changes",
            principal=CHIEF_PRINCIPAL,
            next_holder=CHIEF_PRINCIPAL,
            next_ceiling="needs_implementation",
            now=4,
        )
    finally:
        conn.close()


def test_review_resolution_during_session_preparation_suppresses_duplicate_waiters_and_replay(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.conversation.contracts import PromptDeliveryFate, PromptDeliverySuppressed

    async def exercise() -> None:
        await _start(harness)
        ticket_id, sender_id, message = _review_notice(harness.db_path)
        entered, release = asyncio.Event(), asyncio.Event()
        original_start = _FakeBackendChild.start

        async def delayed_start(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            await original_start(self, *args, **kwargs)
            entered.set()
            await release.wait()

        monkeypatch.setattr(_FakeBackendChild, "start", delayed_start)

        async def send() -> PromptDeliveryFate:
            return await harness.system.send(
                "c",
                text_message_content(message),
                sender_label="Chief",
                sender_message_id=sender_id,
                sender=CHIEF_PRINCIPAL,
                recipient=Principal(PrincipalKind.manager, "reviewer"),
            )

        first = asyncio.create_task(send())
        await entered.wait()
        duplicate = asyncio.create_task(send())
        _resolve_review(harness.db_path, ticket_id)
        release.set()
        fates = await asyncio.gather(first, duplicate)
        assert all(isinstance(fate, PromptDeliverySuppressed) for fate in fates)
        assert isinstance(await send(), PromptDeliverySuppressed)
        assert harness.backend("c").writes == []
        assert not await harness.system.is_running("c")
        discards = [
            e.payload
            for e in await harness.events("c")
            if isinstance(e.payload, PromptDiscardedEventPayload)
        ]
        assert len(discards) == 1
        assert discards[0].sender_message_id == sender_id
        assert discards[0].suppression_reason == "proposal_resolved"
        assert _turn_failure_notice_count(harness.db_path) == 0
        conn = connect(str(harness.db_path))
        try:
            statuses = {
                row[0]
                for row in conn.execute(
                    "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id='c'"
                )
            }
        finally:
            conn.close()
        assert {"unavailable", "suppressed"} <= statuses
        await harness.system.shutdown()
        restarted = _Harness(harness.db_path)
        try:
            replay = await restarted.system.send(
                "c",
                text_message_content(message),
                sender_label="Chief",
                sender_message_id=sender_id,
                sender=CHIEF_PRINCIPAL,
                recipient=Principal(PrincipalKind.manager, "reviewer"),
            )
            assert isinstance(replay, PromptDeliverySuppressed)
            assert restarted.backends == {}
        finally:
            await restarted.system.shutdown()

    _run(exercise)


def test_queue_admission_removes_stale_envelope(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness)
        await harness.system.send("c", text_message_content("Start"), sender_label="Owner")
        ticket_id, sender_id, message = _review_notice(harness.db_path)
        await harness.system.send(
            "c",
            text_message_content(message),
            sender_label="Stale Chief",
            sender_message_id=sender_id,
            sender=CHIEF_PRINCIPAL,
            recipient=Principal(PrincipalKind.manager, "reviewer"),
        )
        await harness.system.send(
            "c",
            text_message_content("Current owner work"),
            sender_label="Owner",
            sender=OWNER_PRINCIPAL,
            recipient=Principal(PrincipalKind.manager, "reviewer"),
        )
        _resolve_review(harness.db_path, ticket_id)
        await harness.complete_turn("c")
        writes = harness.backend("c").writes
        assert len(writes) == 2
        assert message not in writes[-1].text
        assert "Current owner work" in writes[-1].text
        assert harness.backend("c").sender_message_counts[-1] == 1
        prompts = [
            e.payload
            for e in await harness.events("c")
            if isinstance(e.payload, PromptEventPayload)
        ]
        assert [message_content_text(p.content) for p in prompts] == ["Start", "Current owner work"]
        await harness.complete_turn("c")
        assert _turn_failure_notice_count(harness.db_path) == 0

    _run(exercise)


@pytest.mark.parametrize(
    "promotion", [None, HeldPromptPromotionMode.send_now, HeldPromptPromotionMode.steer]
)
def test_obsolete_review_never_reaches_direct_or_promoted_steer(
    harness: _Harness,
    promotion: HeldPromptPromotionMode | None,
) -> None:
    from planner.conversation.contracts import PromptDeliverySuppressed

    async def exercise() -> None:
        await _start(harness)
        await harness.system.send("c", text_message_content("Start"), sender_label="Owner")
        ticket_id, sender_id, message = _review_notice(harness.db_path)
        if promotion is not None:
            await harness.system.send(
                "c",
                text_message_content(message),
                sender_label="Chief",
                sender_message_id=sender_id,
            )
            held = (await harness.system.held_prompts("c"))[0]
        _resolve_review(harness.db_path, ticket_id)
        if promotion is None:
            fate = await harness.system.send(
                "c",
                text_message_content(message),
                sender_label="Chief",
                sender_message_id=sender_id,
                mode=PromptDeliveryMode.steer,
            )
        else:
            promoted = await harness.system.promote_held_prompt("c", held.held_prompt_id, promotion)
            assert promoted is not None
            fate = promoted
        assert isinstance(fate, PromptDeliverySuppressed)
        assert harness.backend("c").sender_written_texts() == ("Start",)
        assert await harness.system.held_prompts("c") == ()
        assert _turn_failure_notice_count(harness.db_path) == 0

    _run(exercise)


def test_rebind_rechecks_review_and_discards_child_with_uncommitted_model(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.conversation.contracts import PromptDeliverySuppressed

    async def exercise() -> None:
        await _start(harness, model="original", reasoning_effort="low")
        await harness.system.send("c", text_message_content("Start"), sender_label="Owner")
        await harness.complete_turn("c")
        ticket_id, sender_id, message = _review_notice(harness.db_path)
        entered, release = asyncio.Event(), asyncio.Event()
        original_start = _FakeBackendChild.start

        async def delayed_start(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            await original_start(self, *args, **kwargs)
            entered.set()
            await release.wait()

        monkeypatch.setattr(_FakeBackendChild, "start", delayed_start)
        harness.backend("c").needs_rebind_once = True
        sending = asyncio.create_task(
            harness.system.send(
                "c",
                text_message_content(message),
                sender_label="Chief",
                sender_message_id=sender_id,
                model_change="new-model",
                reasoning_effort_change="high",
            )
        )
        await entered.wait()
        _resolve_review(harness.db_path, ticket_id)
        release.set()
        assert isinstance(await sending, PromptDeliverySuppressed)
        assert harness.backend("c").sender_written_texts() == ("Start",)
        assert harness.backend("c").live_children == 0
        record = await harness.store.read_conversation("c")
        assert record is not None
        assert (record.model, record.reasoning_effort) == ("original", "low")
        assert not await harness.system.is_running("c")

    _run(exercise)


def test_refused_review_command_is_rechecked_when_its_queue_departure_arrives(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        await _start(harness)
        await harness.system.send("c", text_message_content("Start"), sender_label="Owner")
        ticket_id, sender_id, message = _review_notice(harness.db_path)
        harness.backend("c").steer_outcome = BackendSteerRefused(
            PromptDeliveryRefusalReason.command_cannot_join_running_turn
        )
        fate = await harness.system.send(
            "c",
            text_message_content(message),
            sender_label="Chief",
            sender_message_id=sender_id,
            mode=PromptDeliveryMode.steer,
        )
        assert isinstance(fate, PromptDeliveryQueued)
        _resolve_review(harness.db_path, ticket_id)
        await harness.complete_turn("c")
        assert harness.backend("c").sender_written_texts() == ("Start",)
        assert await harness.system.held_prompts("c") == ()
        assert not await harness.system.is_running("c")
        discards = [
            event.payload
            for event in await harness.events("c")
            if isinstance(event.payload, PromptDiscardedEventPayload)
        ]
        assert len(discards) == 1 and discards[0].suppression_reason == "proposal_resolved"

    _run(exercise)


@pytest.mark.parametrize("resolve_first", [False, True])
def test_chief_to_manager_transfer_dispatches_only_current_work_through_both_doors(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    resolve_first: bool,
) -> None:
    from datetime import UTC, datetime

    from planner.conversation.start_admission import AutomaticStartAdmission
    from planner.core.clock import TestClock
    from planner.manager_wakes import data as wake_data
    from planner.manager_wakes.contracts import WakeBatch
    from planner.manager_wakes.runtime import deliver_batch
    from planner.managers import data as managers_data
    from planner.tickets import data as tickets_data
    from planner.tickets.contracts import TITLE_MAX_CHARS

    admission_now = [0.0]
    harness.system.start_admission = AutomaticStartAdmission(clock=lambda: admission_now[0])

    async def exercise() -> None:
        conn = connect(str(harness.db_path))
        try:
            manager = managers_data.create_manager(
                conn,
                title="New reviewer",
                project_id="project_vylo",
                clock=TestClock(datetime.fromtimestamp(10, UTC)),
            )
            manager_principal = Principal(PrincipalKind.manager, manager.id)
            ticket = tickets_data.create_ticket(
                conn,
                title="Transfer review",
                principal=CHIEF_PRINCIPAL,
                now=1,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type="coding",
                brief="Review",
                stated_ceiling="needs_what_changes",
            )
            worker = Principal(PrincipalKind.ticket, ticket.id)
            tickets_data.file_current_proposal(
                conn,
                ticket.id,
                body="Old review",
                principal=worker,
                now=2,
            )
            old_batch = wake_data.claim_next_batch(conn, process_token="test", now=3)
            assert old_batch is not None
        finally:
            conn.close()

        entered, release = asyncio.Event(), asyncio.Event()
        original_start = _FakeBackendChild.start
        first_start = True

        async def delayed_start(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal first_start
            await original_start(self, *args, **kwargs)
            if first_start:
                first_start = False
                entered.set()
                await release.wait()

        monkeypatch.setattr(_FakeBackendChild, "start", delayed_start)

        async def deliver(batch: WakeBatch) -> bool:
            return await deliver_batch(
                batch,
                connect_database=lambda: connect(str(harness.db_path)),
                conversation_system=harness.system,
                process_token="test",
                now=lambda: 10,
            )

        old_delivery = asyncio.create_task(deliver(old_batch))
        await entered.wait()
        conn = connect(str(harness.db_path))
        try:
            if resolve_first:
                tickets_data.accept_proposal(
                    conn,
                    ticket.id,
                    field="what_changes",
                    principal=CHIEF_PRINCIPAL,
                    next_holder=CHIEF_PRINCIPAL,
                    next_ceiling="needs_implementation",
                    now=4,
                )
            tickets_data.edit_ticket(
                conn,
                ticket.id,
                edit={"manager_id": manager.id, "ceiling_holder": manager_principal},
                principal=OWNER_PRINCIPAL,
                title_max_chars=TITLE_MAX_CHARS,
                now=5,
            )
            if resolve_first:
                tickets_data.file_current_proposal(
                    conn,
                    ticket.id,
                    body="New review",
                    principal=worker,
                    now=6,
                )
            current_batch = wake_data.claim_next_batch(conn, process_token="test", now=10)
            assert current_batch is not None
            assert current_batch.target == manager_principal
        finally:
            conn.close()
        release.set()
        assert not await old_delivery
        admission_now[0] += 1.0  # A new automatic start needs the next pacing opportunity.
        assert await deliver(current_batch)
        assert len(harness.backends) == 2
        all_writes = [write for backend in harness.backends.values() for write in backend.writes]
        assert len(all_writes) == 1
        assert all_writes[0].sender_content is not None
        assert message_content_text(all_writes[0].sender_content) == current_batch.message
        assert old_batch.sender_message_id != current_batch.sender_message_id
        delivered_prompts = [
            event.payload
            for conversation_id in harness.backends
            for event in await harness.events(conversation_id)
            if isinstance(event.payload, PromptEventPayload)
        ]
        assert [prompt.sender_message_id for prompt in delivered_prompts] == [
            current_batch.sender_message_id
        ]
        chief_events = [
            event
            for conversation_id, backend in harness.backends.items()
            if not backend.writes
            for event in await harness.events(conversation_id)
        ]
        assert any(
            isinstance(event.payload, PromptDiscardedEventPayload)
            and event.payload.sender_message_id == old_batch.sender_message_id
            for event in chief_events
        )

    _run(exercise)


def test_review_reply_reads_only_displayed_run_on_queue_admission_and_later_delivery(
    harness: _Harness,
) -> None:
    from fastapi import FastAPI, Request

    from planner.conversation.api import OwnerSendBody, SentTextPiece
    from planner.core.authctx import RequestContext
    from planner.core.clock import RealClock
    from planner.tickets.api import send_to_ticket_conversation

    async def exercise() -> None:
        await _start(harness)
        worker = Principal(PrincipalKind.ticket, "worker")
        _link_test_ticket_to_conversation(harness.db_path, "c", ticket_id=worker.id)
        await harness.system.send("c", text_message_content("busy"), sender_label="system")
        await harness.system.record_message_to_owner(
            "c",
            text_message_content("displayed"),
            sender_label="Worker",
            sender=worker,
            recipient=OWNER_PRINCIPAL,
        )
        displayed = (await harness.events("c"))[-1].sequence
        await harness.system.record_message_to_owner(
            "c",
            text_message_content("not displayed"),
            sender_label="Worker",
            sender=worker,
            recipient=OWNER_PRINCIPAL,
        )
        app = FastAPI()
        app.state.conn_factory = lambda: connect(str(harness.db_path))
        response = await send_to_ticket_conversation(
            worker.id,
            OwnerSendBody(
                conversation_id="c",
                sender_label="owner",
                content=[SentTextPiece(piece="text", text="reply")],
                sender_message_id="bounded-queued",
                owner_read_through_sequence=displayed,
            ),
            Request({"type": "http", "app": app}),
            RequestContext(OWNER_PRINCIPAL),
            RealClock(),
            harness.system,
            harness.message_files,
        )
        assert response["fate"] == "queued"
        record = await harness.store.read_conversation("c")
        assert record is not None and record.owner_read_through_sequence == displayed
        await harness.complete_turn("c")
        record = await harness.store.read_conversation("c")
        assert record is not None and record.owner_read_through_sequence == displayed
        assert "reply" in harness.backend("c").written_texts()[-1]
        duplicate = await harness.system.send(
            "c",
            text_message_content("reply"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=worker,
            sender_message_id="bounded-queued",
            owner_read_through_sequence=displayed,
        )
        assert duplicate == PromptDeliveryStarted()
        assert len(harness.backend("c").writes) == 2

    _run(exercise)


def test_refused_review_reply_does_not_acknowledge_displayed_messages(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(harness)
        worker = Principal(PrincipalKind.ticket, "worker")
        await harness.system.record_message_to_owner(
            "c",
            text_message_content("displayed"),
            sender_label="Worker",
            sender=worker,
            recipient=OWNER_PRINCIPAL,
        )
        harness.backend("c").spawn_fails = True
        fate = await harness.system.send(
            "c",
            text_message_content("reply"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=worker,
            sender_message_id="refused-review",
            owner_read_through_sequence=1,
            record_refusal=True,
        )
        assert isinstance(fate, PromptDeliveryRefused)
        record = await harness.store.read_conversation("c")
        assert record is not None and record.owner_read_through_sequence == 0

    _run(exercise)


def test_uncertain_review_steer_does_not_acknowledge_displayed_messages(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _: True)
        await _start(harness)
        worker = Principal(PrincipalKind.ticket, "worker")
        await harness.system.send("c", text_message_content("busy"), sender_label="system")
        await harness.system.record_message_to_owner(
            "c",
            text_message_content("displayed"),
            sender_label="Worker",
            sender=worker,
            recipient=OWNER_PRINCIPAL,
        )
        displayed = (await harness.events("c"))[-1].sequence
        harness.backend("c").steer_outcome = BackendSteerUncertain()
        fate = await harness.system.send(
            "c",
            text_message_content("reply"),
            sender_label="owner",
            sender=OWNER_PRINCIPAL,
            recipient=worker,
            sender_message_id="uncertain-review",
            mode=PromptDeliveryMode.steer,
            owner_read_through_sequence=displayed,
        )
        assert isinstance(fate, PromptDeliveryUncertain)
        record = await harness.store.read_conversation("c")
        assert record is not None and record.owner_read_through_sequence == 0

    _run(exercise)


def test_identified_review_attachment_retry_is_one_delivery_and_keeps_original_read_bound(
    harness: _Harness,
) -> None:
    import base64

    from planner.conversation.api import SentFilePiece, conversation_message_content
    from planner.conversation.contracts import PromptDeliveryFate

    async def exercise() -> None:
        await _start(harness)
        worker = Principal(PrincipalKind.ticket, "worker")
        await harness.system.record_message_to_owner(
            "c",
            text_message_content("displayed"),
            sender_label="Worker",
            sender=worker,
            recipient=OWNER_PRINCIPAL,
        )
        body = SentFilePiece(
            piece="file",
            data=base64.b64encode(b"reply attachment").decode(),
            media_type="text/plain",
            file_name="reply.txt",
        )

        retry_bound = 1

        async def attempt() -> PromptDeliveryFate:
            content = await conversation_message_content(
                harness.message_files,
                "c",
                [body],
                sender_message_id="attachment-review",
            )
            return await harness.system.send(
                "c",
                content,
                sender_label="owner",
                sender=OWNER_PRINCIPAL,
                recipient=worker,
                sender_message_id="attachment-review",
                owner_read_through_sequence=retry_bound,
            )

        fates = await asyncio.gather(attempt(), attempt())
        assert all(isinstance(fate, PromptDeliveryStarted) for fate in fates)
        assert len(harness.backend("c").writes) == 1
        await harness.system.record_message_to_owner(
            "c",
            text_message_content("arrived after the lost response"),
            sender_label="Worker",
            sender=worker,
            recipient=OWNER_PRINCIPAL,
        )
        retry_bound = (await harness.events("c"))[-1].sequence
        # A retry after a lost response materializes bytes again, then finds its receipt.
        assert isinstance(await attempt(), PromptDeliveryStarted)
        assert len(harness.backend("c").writes) == 1
        record = await harness.store.read_conversation("c")
        assert record is not None and record.owner_read_through_sequence == 1
        body.data = base64.b64encode(b"different reply attachment").decode()
        with pytest.raises(ValueError, match="different message"):
            await attempt()

    _run(exercise)


def test_local_worker_does_not_receive_native_context(harness: _Harness) -> None:
    async def exercise() -> None:
        await _start(
            harness,
            role_materials=ConversationRoleMaterials(
                "Local Worker role.", (("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t_worker"))
            ),
        )
        await harness.system.send("c", text_message_content("Local work"), sender_label="Panels")
        assert harness.backend("c").writes[0].content == text_message_content(
            "Local Worker role.\n\nLocal work"
        )

    _run(exercise)


@pytest.mark.parametrize("cancelled", [False, True])
def test_shutdown_retains_unconfirmed_local_child_and_revokes_callbacks(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    async def exercise() -> None:
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        await harness.settle()
        state = harness.system._conversations["c"]
        child = state.child
        assert child is not None
        backend = harness.backend("c")
        token = backend.live_turn_token
        sink = backend.sink
        assert token is not None and sink is not None
        before = await harness.events("c")

        async def fail_stop() -> None:
            if cancelled:
                raise asyncio.CancelledError
            raise RuntimeError("termination remains unconfirmed")

        monkeypatch.setattr(child, "stop", fail_stop)
        await harness.system.shutdown()
        assert state.child is child
        assert state.child_is_quarantined
        assert state.backend_event_pump is None
        assert harness.system._runtime_ownership.closed
        await sink.report(
            token, AgentMessageCompleted(content=text_message_content("late callback"))
        )
        assert await harness.events("c") == before

    _run(exercise)


@pytest.mark.parametrize("mode", [PromptDeliveryMode.queue, PromptDeliveryMode.send_now])
def test_spawn_refusal_retains_native_detail_after_rebind(
    harness: _Harness, mode: PromptDeliveryMode
) -> None:
    from planner.conversation.events import PromptDeliveryRefusedEventPayload

    async def exercise() -> None:
        await _start(harness)
        await harness.system.send("c", text_message_content("first"), sender_label="owner")
        await harness.complete_turn("c")
        harness.backend("c").needs_rebind_once = True
        harness.backend("c").spawn_fails = True
        fate = await harness.system.send(
            "c",
            text_message_content("retry"),
            sender_label="owner",
            sender_message_id="spawn-refusal",
            mode=mode,
        )
        assert isinstance(fate, PromptDeliveryRefused)
        assert fate.refusal_reason is PromptDeliveryRefusalReason.backend_did_not_start
        events = await harness.events("c")
        refused = [
            e.payload for e in events if isinstance(e.payload, PromptDeliveryRefusedEventPayload)
        ]
        assert len(refused) == 1
        assert refused[0].refusal_detail == "c"
        assert refused[0].sender_message_id == "spawn-refusal"

    _run(exercise)


def test_system_prompt_provenance_keeps_agent_only_attention_share_usable(
    harness: _Harness,
) -> None:
    """A real automatic prompt must not look like an unknown historical owner message."""
    from datetime import date, datetime
    from zoneinfo import ZoneInfo

    from planner.attention.actions import read_summary
    from planner.attention.contracts import SummaryRequest

    start = int(datetime(2026, 11, 2, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())

    async def exercise() -> None:
        harness.clock.now = start - 86400
        await _start(harness, "attention-system")
        _link_test_ticket_to_conversation(
            harness.db_path, "attention-system", ticket_id="t_agent_only"
        )
        conn = connect(str(harness.db_path))
        try:
            conn.execute("UPDATE attention_history_metadata SET started_at=?", (start - 86400,))
        finally:
            conn.close()
        harness.clock.now = start + 100
        await harness.system.send(
            "attention-system",
            text_message_content("Run the next worker step"),
            sender_label="Panels loop",
        )
        queued = await harness.system.send(
            "attention-system",
            text_message_content("A queued worker step"),
            sender_label="Panels loop",
            mode=PromptDeliveryMode.queue,
        )
        assert isinstance(queued, PromptDeliveryQueued)
        await harness.complete_turn("attention-system")
        await harness.complete_turn("attention-system")
        prompts = [
            event.payload
            for event in await harness.events("attention-system")
            if isinstance(event.payload, PromptEventPayload)
        ]
        assert len(prompts) == 2
        assert all(prompt.system_generated is True for prompt in prompts)
        assert all(prompt.sender is None for prompt in prompts)
        conn = connect(str(harness.db_path))
        try:
            result = read_summary(
                conn, SummaryRequest(date(2026, 11, 2), date(2026, 11, 2)), now=start + 86401
            )
        finally:
            conn.close()
        figures = result["totals"]["current"]
        assert figures["owner_messages"]["value"] == 0
        assert figures["owner_messages"]["coverage_status"] == "complete"
        assert figures["active_ticket_count"]["value"] == 1
        assert figures["without_owner_involvement_share"]["value"] == 1

    _run(exercise)


def test_resumed_manager_receives_canonical_role_and_environment(harness: _Harness) -> None:
    """A retained transcript must not suppress the new Manager role on its next turn."""
    from planner.runtime.logic.conversation_start_resolution import manager_role_materials

    async def exercise() -> None:
        materials = manager_role_materials("si_retained", Path("/shared/workspace"))
        await _start(harness, "manager-resume", role_materials=materials)
        await harness.system.send(
            "manager-resume", text_message_content("first"), sender_label="owner"
        )
        await harness.complete_turn("manager-resume")
        await harness.system.send(
            "manager-resume", text_message_content("next"), sender_label="owner"
        )
        assert harness.backend("manager-resume").written_texts() == (
            materials.role_text + "\n\nfirst",
            materials.role_text + "\n\nnext",
        )
        record = await harness.store.read_conversation("manager-resume")
        assert record is not None
        assert dict(record.identity_environment_variables)["PLAN_MANAGER_ID"] == "si_retained"

    _run(exercise)


# --- background tasks ------------------------------------------------------------------


async def _report_background_task_started(
    harness: _Harness, conversation_id: str, task_id: str, task_description: str
) -> None:
    sink = harness.backend(conversation_id).sink
    assert sink is not None
    await sink.report(
        None, BackgroundTaskStarted(task_id=task_id, task_description=task_description)
    )
    await harness.settle()


def test_a_live_background_task_keeps_the_child_and_shows_working_after_the_turn(
    harness: _Harness,
) -> None:
    """The janitor leaves a busy session alone, and its clock restarts at the task's end.

    An open conversation is woken for each change and reads the same live tasks the
    working signal does, so the sidebar and the conversation agree.
    """
    from planner.conversation.signals import read_conversation_signals
    from planner.conversation.views.status import read_status

    def conversation_background_tasks() -> list[str]:
        conn = connect(str(harness.db_path))
        try:
            status = read_status(conn, "c", "full", harness.live_tail.views, running=False)
        finally:
            conn.close()
        return [task["task_description"] for task in status["background_tasks"]]

    async def exercise() -> None:
        await _start(harness, backend_key=ConversationBackendKey.claude)
        view = harness.live_tail.views.subscribe("c", "full", transcript=True, turn_key=None)
        await harness.system.send("c", text_message_content("run the suite"), sender_label="o")
        view.wake.clear()
        await _report_background_task_started(harness, "c", "task-1", "Run the test suite")
        assert view.wake.is_set()
        assert conversation_background_tasks() == ["Run the test suite"]
        await harness.complete_turn("c")
        backend = harness.backend("c")

        harness.clock.advance(31 * 60)
        await harness.system._sweep_idle_children()
        assert backend.stops == 0
        assert await harness.system.is_running("c") is False
        signals = await read_conversation_signals(harness.system, harness.store, {"c"})
        assert signals["c"].signal["working"] is True

        assert backend.sink is not None
        view.wake.clear()
        await backend.sink.report(
            None, BackgroundTaskEnded(task_id="task-1", task_status="completed")
        )
        await harness.settle()
        assert view.wake.is_set()
        assert conversation_background_tasks() == []
        signals = await read_conversation_signals(harness.system, harness.store, {"c"})
        assert signals["c"].signal["working"] is False
        await harness.system._sweep_idle_children()
        assert backend.stops == 0
        harness.clock.advance(31 * 60)
        await harness.system._sweep_idle_children()
        assert backend.stops == 1
        assert [prompt[0] for prompt in await harness.recorded_prompts("c")] == ["run the suite"]

    _run(exercise)


@pytest.mark.parametrize(
    "how",
    (
        "stream_ended",
        "stopped_as_the_stream_ended",
        "stopped_then_the_agent_acted",
        "child_replaced",
        "server_restarted",
        "killed",
    ),
)
def test_a_background_task_lost_with_its_session_is_told_to_the_agent(
    harness: _Harness, tmp_path: Path, how: str
) -> None:
    """One exact Panels prompt per lost task; a killed conversation is not woken."""
    lost_prompt = (
        'Your background task "Run the test suite" stopped because your session ended. '
        "It did not finish. Read its output, then start it again or report what happened."
    )

    async def exercise() -> None:
        await _start(harness, backend_key=ConversationBackendKey.claude)
        await harness.system.send("c", text_message_content("run the suite"), sender_label="o")
        await _report_background_task_started(harness, "c", "task-1", "Run the test suite")
        if how != "killed":
            await harness.complete_turn("c")
        system = harness.system
        if how == "stream_ended":
            sink = harness.backend("c").sink
            assert sink is not None
            await sink.report(None, BackendStreamEnded())
        elif how == "stopped_as_the_stream_ended":
            # Claude stops its tasks as it is terminated, so the task has left the record
            # and no screen shows it, but the stream ends before the agent acts again.
            sink = harness.backend("c").sink
            assert sink is not None
            await sink.report(None, BackgroundTaskEnded(task_id="task-1", task_status="killed"))
            await sink.report(None, BackendStreamEnded())
        elif how == "stopped_then_the_agent_acted":
            # The agent stopped the task itself and went on working: not lost.
            sink = harness.backend("c").sink
            assert sink is not None
            await sink.report(None, BackgroundTaskEnded(task_id="task-1", task_status="killed"))
            await harness.settle()
            await system.send("c", text_message_content("carry on"), sender_label="o")
            await harness.complete_turn("c")
            await sink.report(None, BackendStreamEnded())
        elif how == "child_replaced":
            harness.backend("c").needs_rebind_once = True
            await system.send(
                "c", text_message_content("switch model"), sender_label="o", model_change="m2"
            )
            # The notice waits behind the turn that replaced the child.
            await harness.complete_turn("c")
        elif how == "server_restarted":
            await system.shutdown()
            restarted = _Harness(tmp_path / "conversations.db")
            restarted.backends = harness.backends
            system = restarted.system
            await system.tell_agents_of_background_tasks_lost_at_restart()
        else:
            # A cancel that fails makes the kill stop the child itself, mid-kill.
            harness.backend("c").cancels_raise_something_unnamed = True
            await system.kill("c")
        await system.wait_until_quiescent()

        prompts = [
            (message_content_text(event.payload.content), event.payload.sender_label)
            for event in await harness.store.read_events_after("c", 0)
            if isinstance(event.payload, PromptEventPayload)
        ]
        told = [prompt for prompt in prompts if prompt[0] == lost_prompt]
        assert told == (
            [] if how in ("killed", "stopped_then_the_agent_acted") else [(lost_prompt, "Panels")]
        )
        facts = await harness.store.attention_facts({"c"})
        assert facts["c"].has_live_background_task is False
        if how == "server_restarted":
            await system.shutdown()

    _run(exercise)


def test_automatic_queue_releases_slots_and_coalesces_one_dispatch_permit(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: held supervisor messages consume slots or priority before they can run.

    Existing delivery fates do not prove admission retirement or coalesced permits.
    Reuse the real queue, SQLite store, and existing fake backend without another host.
    """
    from planner.conversation.start_admission import AutomaticStartAdmission
    from planner.conversation.start_admission.contracts import StartRequest

    async def exercise() -> None:
        now = [0.0]
        gate = AutomaticStartAdmission(clock=lambda: now[0])
        harness.system.start_admission = gate
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        await _start(harness, "supervisor")
        await _start(harness, "worker")
        await harness.system.send(
            "supervisor", text_message_content("incumbent"), sender_label="owner"
        )
        identities = tuple(f"supervisor-auto-{i}" for i in range(4))
        for identity in identities:
            assert gate.try_start(StartRequest(identity, "supervisor", "home"))
            fate = await harness.system.send(
                "supervisor",
                text_message_content(identity),
                sender_label="Panels",
                sender_message_id=identity,
            )
            assert isinstance(fate, PromptDeliveryQueued)
            now[0] += 1.0
        assert {snapshot.state for snapshot in gate.snapshots()} == {"queued"}
        assert all(snapshot.permit_identity is None for snapshot in gate.snapshots())
        assert gate.try_start(StartRequest("home-worker", "worker", "home"))
        assert isinstance(
            await harness.system.send(
                "worker",
                text_message_content("home work"),
                sender_label="Panels",
                sender_message_id="home-worker",
            ),
            PromptDeliveryStarted,
        )

        await harness.agent_message("worker", text_message_content("Worker accepted its prompt"))
        held = await harness.system.held_prompts("supervisor")
        assert await harness.system.discard_held_prompt("supervisor", held[0].held_prompt_id)
        assert isinstance(
            await harness.system.promote_held_prompt(
                "supervisor", held[1].held_prompt_id, HeldPromptPromotionMode.steer
            ),
            PromptDeliveryInjected,
        )
        assert {snapshot.request.identity for snapshot in gate.snapshots()} == set(identities[2:])
        # The next head is eligible now, but cannot reuse the Worker's pacing instant.
        await harness.complete_turn("supervisor")
        assert len(harness.backend("supervisor").writes) == 2  # incumbent and accepted steer
        now[0] += 1.0
        backend = harness.backend("supervisor")
        backend.write_has_begun = asyncio.Event()
        backend.writes_wait_for_release = asyncio.Event()
        drain = asyncio.create_task(
            harness.system._drain_held_prompts(harness.system._conversations["supervisor"])
        )
        try:
            await asyncio.wait_for(backend.write_has_begun.wait(), 2)
            snapshots = gate.snapshots()
            assert {snapshot.request.identity for snapshot in snapshots} == set(identities[2:])
            assert {snapshot.state for snapshot in snapshots} == {"active"}
            assert len({snapshot.permit_identity for snapshot in snapshots}) == 1
            assert snapshots[0].permit_identity is not None
            assert len({snapshot.turn for snapshot in snapshots}) == 1
            backend.writes_wait_for_release.set()
            await drain
        finally:
            backend.writes_wait_for_release.set()
            await asyncio.gather(drain, return_exceptions=True)
        await harness.agent_message("supervisor", text_message_content("Notice accepted"))
        assert gate.snapshots() == ()
        assert backend.sender_message_counts[-1] == 2
        assert all(identity in backend.writes[-1].text for identity in identities[2:])
        assert await harness.system.held_prompts("supervisor") == ()

    _run(exercise)


def test_queued_automatic_cancel_after_bind_before_wire_releases_slot_and_priority(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Risk: no-wire cancellation leaks a bound slot or eligible supervisor priority.

    Existing uncertain-wire cases require retention, so they cannot prove this cleanup.
    Use the existing queue/backend host and inject cancellation at the awaited store call.
    """
    from planner.conversation.start_admission import AutomaticStartAdmission
    from planner.conversation.start_admission.contracts import StartRequest

    async def exercise() -> None:
        now = [0.0]
        gate = AutomaticStartAdmission(clock=lambda: now[0])
        harness.system.start_admission = gate
        await _start(harness, "supervisor")
        await _start(harness, "worker")
        _link_test_ticket_to_conversation(harness.db_path, "worker", ticket_id="t_admission")
        await harness.system.send(
            "supervisor", text_message_content("incumbent"), sender_label="owner"
        )
        assert gate.try_start(StartRequest("cancel-before-wire", "supervisor", "home"))
        assert isinstance(
            await harness.system.send(
                "supervisor",
                text_message_content("owed notice"),
                sender_label="Panels",
                sender_message_id="cancel-before-wire",
            ),
            PromptDeliveryQueued,
        )
        # This test owns dequeue dispatch, independently of refunded queue-only credit.
        original_drain = harness.system._drain_held_prompts

        async def hold_drain(_state: Any) -> None:
            return None

        monkeypatch.setattr(harness.system, "_drain_held_prompts", hold_drain)
        await harness.complete_turn("supervisor")
        monkeypatch.setattr(harness.system, "_drain_held_prompts", original_drain)
        conn = connect(str(harness.db_path))
        try:
            claim_before = tuple(
                conn.execute(
                    "SELECT worker_step_claim,worker_step_claim_revision FROM tickets WHERE id=?",
                    ("t_admission",),
                ).fetchone()
            )
        finally:
            conn.close()
        original_bind = harness.store.bind_interruption_delivery
        reached = False

        async def cancel_after_bind(*args: Any, **kwargs: Any) -> Any:
            nonlocal reached
            reached = True
            conn = connect(str(harness.db_path))
            try:
                assert (
                    conn.execute(
                        "SELECT COUNT(*) FROM automatic_start_bindings WHERE identity=?",
                        ("cancel-before-wire",),
                    ).fetchone()[0]
                    == 1
                )
            finally:
                conn.close()
            raise asyncio.CancelledError()

        monkeypatch.setattr(harness.store, "bind_interruption_delivery", cancel_after_bind)
        now[0] += 1.0
        state = harness.system._conversations["supervisor"]
        with pytest.raises(asyncio.CancelledError):
            await harness.system._drain_held_prompts(state)
        monkeypatch.setattr(harness.store, "bind_interruption_delivery", original_bind)
        assert reached
        assert harness.backend("supervisor").written_texts() == ("incumbent",)
        assert not state.child_is_quarantined
        snapshots = gate.snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].request.identity == "cancel-before-wire"
        assert snapshots[0].state == "queued"
        assert snapshots[0].permit_identity is None
        assert snapshots[0].turn is None
        assert [
            message_content_text(item.content)
            for item in await harness.system.held_prompts("supervisor")
        ] == ["owed notice"]
        conn = connect(str(harness.db_path))
        try:
            assert conn.execute("SELECT COUNT(*) FROM automatic_start_bindings").fetchone()[0] == 0
            assert (
                tuple(
                    conn.execute(
                        "SELECT worker_step_claim,worker_step_claim_revision "
                        "FROM tickets WHERE id=?",
                        ("t_admission",),
                    ).fetchone()
                )
                == claim_before
            )
        finally:
            conn.close()
        now[0] += 1.0
        assert gate.try_start(StartRequest("worker-after-cancel", "worker", "home"))
        assert isinstance(
            await harness.system.send(
                "worker",
                text_message_content("home work"),
                sender_label="Panels",
                sender_message_id="worker-after-cancel",
            ),
            PromptDeliveryStarted,
        )
        assert harness.backend("worker").written_texts() == ("home work",)

    _run(exercise)
