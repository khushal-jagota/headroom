"""Binds the real conversation system, over real child processes, to the conformance harness.

Nothing here is a stand-in for the conversation system. The subject is
``SqliteProcessConversationSystem`` over a real SQLite file, every backend key mapped to
the real hermes ACP adapter, and every child a real process on the other end of a real
Agent Client Protocol wire. What is scripted is the agent at the far end: it does what a
test tells it and keeps its own account of what reached it, which is what makes
``backend_writes`` an answer from the backend rather than the system's own report.

Three things a binder has to get right, and how this one does.

**Timing.** Every driving method waits for the consequence it caused, never for a length of
time. Telling the agent to finish its turn returns when the adapter has reported that
ending *and* the system has finished everything the ending set off — a held message
dequeued, written, recorded. The first is a signal from the sink the adapter reports to;
the second is the system's own quiescence.

**Asking a separate process what it has seen.** The bytes of a prompt are on the wire
before ``send`` returns, but the agent at the other end is a process with its own schedule,
so an account read at that instant may not have caught up. Every read waits until the
agent's account holds everything already known to be on its way — never for anything more
than that, so a message that was only queued stays absent however long anyone looks.

**Where an arm belongs.** The three armed failures are three different boundaries and each
is armed where it really happens: a spawn that fails is a launch of something that is not
there, a session that will not load is an agent told to refuse the session, and a write
that fails is an agent that has closed the pipe it reads from and stays alive without it.
"""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.support.conversation_contract_conformance import (
    BackendWrite,
    RecordedFact,
    RecordedFactKind,
    RecordedTurnEnding,
)
from tests.support.conversation_scripted_acp_agent import (
    ARM_BREAK_WIRE_ON_SESSION,
    ARM_REJECT_LOAD_SESSION,
    ARM_REJECT_NEW_SESSION,
    ScriptedAcpAgentControl,
    scripted_acp_agent_launch,
)

from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendPermissionAsk,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.backends.hermes_acp import (
    AcpChildLaunch,
    HermesAcpBackendChild,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ConversationBackendKey,
    ConversationSystem,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    ModelChangedEventPayload,
    PermissionAnsweredEventPayload,
    PermissionAskedEventPayload,
    PlanEntry,
    PromptDeliveryRefusedEventPayload,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    ToolCallStatus,
    TurnEndedEventPayload,
    UserInputAnswer,
)
from planner.conversation.live_tail import ConversationLiveTail, ConversationTailSubscription
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    MessagePiece,
    MessageText,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore, StoredConversationEvent
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.db import connect, create_schema

# A launch of something that is not there, which is what a backend that will not spawn is.
_NOTHING_TO_LAUNCH = AcpChildLaunch(argv=("/nonexistent/panels-conversation-backend",))

_RECORDED_ENDINGS = {
    ConversationTurnEnding.completed: RecordedTurnEnding.completed,
    ConversationTurnEnding.failed: RecordedTurnEnding.failed,
    ConversationTurnEnding.interrupted: RecordedTurnEnding.interrupted,
}


class _Pulse:
    """A wake-up for waiters, sent every time the backend side reports anything."""

    def __init__(self) -> None:
        self._something_happened = asyncio.Event()

    def send(self) -> None:
        self._something_happened.set()

    async def wait_until(self, condition: Callable[[], bool]) -> None:
        while not condition():
            self._something_happened.clear()
            if condition():
                return
            await self._something_happened.wait()


@dataclass
class _ScriptedConversation:
    """One conversation's scripted agent: how to reach it, and what is owed to it.

    The expected counts are not an account of anything. They are how a reader of the
    agent's account knows it has caught up: a write the adapter got onto the wire is on its
    way to a process that will record it, so a read waits for that many and no more.
    """

    conversation_id: str
    control: ScriptedAcpAgentControl
    pulse: _Pulse
    arms: set[str] = field(default_factory=set)
    spawn_fails: bool = False
    expected_prompt_writes: int = 0
    expected_cancellations: int = 0
    expected_permission_answers: int = 0
    turn_endings_reported: int = 0
    permission_asks_reported: int = 0
    ask_ids_in_order: list[str] = field(default_factory=list)
    # What agents before this one saw. A conversation outlives its children — one whose
    # child was stopped and started again is the same agent on the same session — so what
    # the backend side has been told is not reset by a respawn.
    writes_to_agents_before_this_one: list[dict[str, Any]] = field(default_factory=list)
    cancellations_before_this_agent: int = 0
    asks_before_this_agent: list[dict[str, Any]] = field(default_factory=list)


class _ObservingSink:
    """Passes the adapter's news through, and says out loud that it went past.

    It changes nothing and asserts nothing: it is how a test knows the adapter has already
    told the core something, which is the moment from which the core's own quiescence is
    worth waiting for.
    """

    def __init__(self, sink: BackendEventSink, conversation: _ScriptedConversation) -> None:
        self._sink = sink
        self._conversation = conversation

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        await self._sink.agent_message_delta(turn_token, text_delta)

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        await self._sink.model_thinking_happened(turn_token)

    async def plan_updated(
        self, turn_token: TurnToken, entries: tuple[PlanEntry, ...]
    ) -> None:
        await self._sink.plan_updated(turn_token, entries)

    async def agent_message_completed(
        self, turn_token: TurnToken, content: MessageContent
    ) -> None:
        await self._sink.agent_message_completed(turn_token, content)

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        await self._sink.tool_call_started(
            turn_token,
            tool_call_id=tool_call_id,
            title=title,
            tool_kind=tool_kind,
            detail=detail,
        )

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        await self._sink.tool_call_progress(
            turn_token, tool_call_id=tool_call_id, detail=detail
        )

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        await self._sink.tool_call_finished(
            turn_token,
            tool_call_id=tool_call_id,
            tool_call_status=tool_call_status,
            detail=detail,
        )

    async def permission_ask_raised(
        self, turn_token: TurnToken, ask: BackendPermissionAsk
    ) -> None:
        await self._sink.permission_ask_raised(turn_token, ask)
        self._conversation.ask_ids_in_order.append(ask.ask_id)
        self._conversation.permission_asks_reported += 1
        self._conversation.pulse.send()

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        await self._sink.user_input_requested(turn_token, request)

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        await self._sink.user_input_failed(
            turn_token, request_id=request_id, detail=detail
        )

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        await self._sink.turn_ended(
            turn_token,
            ending=ending,
            error_summary=error_summary,
            standard_error_tail=standard_error_tail,
        )
        self._conversation.turn_endings_reported += 1
        self._conversation.pulse.send()

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        await self._sink.token_usage_reported(
            turn_token,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens,
            cost_usd=cost_usd,
        )

    async def context_compacted(self, turn_token: TurnToken) -> None:
        await self._sink.context_compacted(turn_token)

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        await self._sink.vendor_session_cursor_rebound(vendor_session_cursor)

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        await self._sink.composer_catalog_reported(composer_catalog)


class _CountedChild:
    """The real adapter, with a tally of what it has put on the wire.

    Every call goes to the hermes adapter untouched. What is kept is only how much is
    already on its way to the agent, so a read of the agent's account knows how far to wait
    for it.
    """

    def __init__(self, child: HermesAcpBackendChild, conversation: _ScriptedConversation) -> None:
        self._child = child
        self._conversation = conversation

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        await self._child.start(resolved_start, vendor_session_cursor=vendor_session_cursor)

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
    ) -> None:
        await self._child.write_prompt(
            turn_token,
            content,
            sender_content=sender_content,
            sender_label=sender_label,
            mode=mode,
            model_change=model_change,
            reasoning_effort_change=reasoning_effort_change,
        )
        self._conversation.expected_prompt_writes += 1

    async def steer(self, content: MessageContent, *, sender_label: str) -> None:
        await self._child.steer(content, sender_label=sender_label)
        self._conversation.expected_prompt_writes += 1

    async def cancel_running_turn(self) -> None:
        await self._child.cancel_running_turn()
        self._conversation.expected_cancellations += 1

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        await self._child.answer_permission_ask(ask_id, option_id)
        self._conversation.expected_permission_answers += 1

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        await self._child.answer_user_input(request_id, answers)

    async def stop(self) -> None:
        # Read the agent one last time while it is still there, so what it saw stays with
        # the conversation rather than going with the process.
        await self._conversation_account_carried_forward()
        await self._child.stop()

    async def _conversation_account_carried_forward(self) -> None:
        conversation = self._conversation
        report = await conversation.control.send({"command": "report"})
        if report is not None:
            conversation.writes_to_agents_before_this_one.extend(report["prompt_writes"])
            conversation.cancellations_before_this_agent += int(report["cancellations"])
            conversation.asks_before_this_agent.extend(report["asks"])
        # A new agent has seen nothing yet, so nothing is owed to it either.
        conversation.expected_prompt_writes = 0
        conversation.expected_cancellations = 0
        conversation.expected_permission_answers = 0


class ConversationSystemUnderTest:
    """The real system, its real children, and their scripted agents' own account."""

    def __init__(
        self,
        system: SqliteProcessConversationSystem,
        store: ConversationStore,
        socket_directory: Path,
        live_tail: ConversationLiveTail,
    ) -> None:
        self._system = system
        self._store = store
        self._socket_directory = socket_directory
        self._live_tail = live_tail
        self._conversations: dict[str, _ScriptedConversation] = {}

    @property
    def system(self) -> ConversationSystem:
        return self._system

    def watch(self, conversation_id: str) -> ConversationTailSubscription:
        """Watch a conversation the way the browser does, for what is shown and not kept."""
        return self._live_tail.subscribe(conversation_id)

    # --- making the children ---------------------------------------------------------------

    def make_child(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _CountedChild:
        conversation = self._conversation(resolved_start.conversation_id)
        launch = (
            _NOTHING_TO_LAUNCH
            if conversation.spawn_fails
            else scripted_acp_agent_launch(
                control_socket_path=conversation.control.socket_path, arms=conversation.arms
            )
        )
        child = HermesAcpBackendChild(
            launch=launch,
            resolved_start=resolved_start,
            event_sink=_ObservingSink(event_sink, conversation),
            message_files=message_files,
        )
        return _CountedChild(child, conversation)

    def _conversation(self, conversation_id: str) -> _ScriptedConversation:
        conversation = self._conversations.get(conversation_id)
        if conversation is None:
            socket_path = self._socket_directory / f"s{len(self._conversations)}.sock"
            conversation = _ScriptedConversation(
                conversation_id=conversation_id,
                control=ScriptedAcpAgentControl(str(socket_path)),
                pulse=_Pulse(),
            )
            self._conversations[conversation_id] = conversation
        return conversation

    # --- the backend side's own account -----------------------------------------------------

    async def _account(self, conversation_id: str) -> dict[str, Any]:
        """What the agent has seen, once it holds everything already on its way to it."""
        conversation = self._conversation(conversation_id)
        while True:
            report = await conversation.control.send({"command": "report"})
            if report is None:
                # No agent is listening: none was ever spawned, or the one that was has
                # gone. Whatever agents before it were told still stands.
                return _everything_this_conversation_has_told_its_backend(conversation, None)
            if (
                len(report["prompt_writes"]) >= conversation.expected_prompt_writes
                and report["cancellations"] >= conversation.expected_cancellations
                and report["answered"] >= conversation.expected_permission_answers
            ):
                return _everything_this_conversation_has_told_its_backend(conversation, report)
            await asyncio.sleep(0)

    async def agent_account(self, conversation_id: str) -> dict[str, Any]:
        """The scripted agent's own report, once it holds everything on its way to it."""
        return await self._account(conversation_id)

    async def tell_agent(
        self, conversation_id: str, command: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Send one command to a conversation's scripted agent."""
        return await self._conversation(conversation_id).control.send(command)

    async def recorded_events(self, conversation_id: str) -> tuple[StoredConversationEvent, ...]:
        """Every row the system wrote for this conversation, kinds and all."""
        return await self._store.read_events_after(conversation_id, 0)

    async def backend_writes(self, conversation_id: str) -> tuple[BackendWrite, ...]:
        report = await self._account(conversation_id)
        return tuple(
            BackendWrite(
                content=_message_from_reported_blocks(write["blocks"]),
                sender_label=write["sender_label"],
                mode=PromptDeliveryMode(write["delivery_mode"]),
            )
            for write in report["prompt_writes"]
        )

    async def backend_permission_answer(self, conversation_id: str, ask_id: str) -> str | None:
        conversation = self._conversation(conversation_id)
        if ask_id not in conversation.ask_ids_in_order:
            return None
        raised_at = conversation.ask_ids_in_order.index(ask_id)
        report = await self._account(conversation_id)
        asks = report["asks"]
        if raised_at >= len(asks):
            return None
        answer = asks[raised_at]["answer"]
        return None if answer is None else str(answer)

    async def backend_cancellations(self, conversation_id: str) -> int:
        report = await self._account(conversation_id)
        return int(report["cancellations"])

    async def backend_model(self, conversation_id: str) -> str | None:
        report = await self._account(conversation_id)
        model = report.get("model")
        return None if model is None else str(model)

    async def backend_reasoning_effort(self, conversation_id: str) -> str | None:
        report = await self._account(conversation_id)
        effort = report.get("reasoning_effort")
        return None if effort is None else str(effort)

    # --- driving the backend -----------------------------------------------------------------

    async def settle(self) -> None:
        await self._system.wait_until_quiescent()

    async def complete_running_turn(self, conversation_id: str) -> None:
        await self._end_the_turn(conversation_id, {"command": "complete_turn"})

    async def fail_running_turn(self, conversation_id: str) -> None:
        await self._end_the_turn(conversation_id, {"command": "fail_turn"})

    async def _end_the_turn(self, conversation_id: str, command: dict[str, Any]) -> None:
        conversation = self._conversation(conversation_id)
        endings_before = conversation.turn_endings_reported
        await conversation.control.send(command)
        await conversation.pulse.wait_until(
            lambda: conversation.turn_endings_reported > endings_before
        )
        await self.settle()

    async def raise_permission_ask(self, conversation_id: str) -> str:
        conversation = self._conversation(conversation_id)
        asks_before = conversation.permission_asks_reported
        await conversation.control.send({"command": "raise_permission_ask"})
        await conversation.pulse.wait_until(
            lambda: conversation.permission_asks_reported > asks_before
        )
        await self.settle()
        return conversation.ask_ids_in_order[-1]

    async def answer_permission_ask(
        self, conversation_id: str, ask_id: str, option_id: str
    ) -> bool:
        landed = await self._system.answer_permission_ask(conversation_id, ask_id, option_id)
        await self.settle()
        return landed

    # --- the three armed failures, each at its own boundary ------------------------------------

    async def arm_backend_start_failure(self, conversation_id: str) -> None:
        self._conversation(conversation_id).spawn_fails = True

    async def arm_session_load_failure(self, conversation_id: str) -> None:
        conversation = self._conversation(conversation_id)
        conversation.arms.add(ARM_REJECT_NEW_SESSION)
        conversation.arms.add(ARM_REJECT_LOAD_SESSION)

    async def arm_backend_write_failure(self, conversation_id: str) -> None:
        conversation = self._conversation(conversation_id)
        # An agent spawned from here on breaks its wire as soon as it has a session on the
        # model the conversation named, so the first prompt write is the first thing that
        # fails. One that is already running is told to break it now.
        conversation.arms.add(ARM_BREAK_WIRE_ON_SESSION)
        await conversation.control.send({"command": "break_wire"})

    # --- what the system recorded ---------------------------------------------------------------

    async def recorded_facts(self, conversation_id: str) -> tuple[RecordedFact, ...]:
        events = await self._store.read_events_after(conversation_id, 0)
        return tuple(fact for event in events if (fact := _recorded_fact(event)) is not None)

    # --- teardown -----------------------------------------------------------------------------

    async def close(self) -> None:
        for conversation in self._conversations.values():
            await conversation.control.send({"command": "shutdown"})
        await self._system.shutdown()


def _everything_this_conversation_has_told_its_backend(
    conversation: _ScriptedConversation, live: dict[str, Any] | None
) -> dict[str, Any]:
    """One conversation's backend account: the agent running now, and the ones before it."""
    carried: dict[str, Any] = {
        "prompt_writes": list(conversation.writes_to_agents_before_this_one),
        "cancellations": conversation.cancellations_before_this_agent,
        "asks": list(conversation.asks_before_this_agent),
        "answered": sum(
            1 for ask in conversation.asks_before_this_agent if ask["answer"] is not None
        ),
    }
    if live is None:
        return carried
    merged = dict(live)
    merged["prompt_writes"] = carried["prompt_writes"] + list(live["prompt_writes"])
    merged["cancellations"] = carried["cancellations"] + int(live["cancellations"])
    merged["asks"] = carried["asks"] + list(live["asks"])
    merged["answered"] = carried["answered"] + int(live["answered"])
    return merged


def _message_from_reported_blocks(blocks: list[dict[str, Any]]) -> MessageContent:
    """What the scripted agent says actually arrived, as a message again.

    The agent reports the blocks it read off the wire, so this is the backend's own
    account of the message rather than the conversation system's — which is the whole
    point of asking the backend what it got.
    """
    return tuple(_piece_from_reported_block(block) for block in blocks)


def _piece_from_reported_block(block: dict[str, Any]) -> MessagePiece:
    match block["piece"]:
        case "text":
            return MessageText(text=str(block["text"]))
        case "image":
            return MessageImage(
                stored_file_id=_ARRIVED_AS_BYTES, media_type=str(block["media_type"])
            )
    raise AssertionError(f"the scripted agent reported a block nobody reads: {block}")


# A stand-in name for a picture or a sound that arrived as bytes. ACP carries the bytes
# rather than a name, so the agent has no id to report back; an exercise that cares which
# file arrived compares the bytes themselves.
_ARRIVED_AS_BYTES = "f_arrived"


def _recorded_fact(event: StoredConversationEvent) -> RecordedFact | None:
    """One stored row in the suite's vocabulary, or nothing when it has no word for it."""
    payload = event.payload
    match payload:
        case PromptEventPayload():
            return RecordedFact(
                kind=RecordedFactKind.prompt_delivered,
                content=payload.content,
                sender_label=payload.sender_label,
                mode=payload.mode,
            )
        case PromptDeliveryRefusedEventPayload():
            return RecordedFact(
                kind=RecordedFactKind.prompt_delivery_refused,
                content=payload.content,
                sender_label=payload.sender_label,
                mode=payload.mode,
                refusal_reason=payload.refusal_reason,
            )
        case PromptDiscardedEventPayload():
            return RecordedFact(
                kind=RecordedFactKind.prompt_discarded,
                content=payload.content,
                sender_label=payload.sender_label,
            )
        case TurnEndedEventPayload():
            return RecordedFact(
                kind=RecordedFactKind.turn_ended,
                turn_ending=_RECORDED_ENDINGS[payload.ending],
            )
        case PermissionAskedEventPayload():
            return RecordedFact(
                kind=RecordedFactKind.permission_asked, permission_ask_id=payload.ask_id
            )
        case PermissionAnsweredEventPayload():
            return RecordedFact(
                kind=RecordedFactKind.permission_answered, permission_ask_id=payload.ask_id
            )
        case ModelChangedEventPayload():
            return RecordedFact(
                kind=RecordedFactKind.model_changed,
                model=payload.model,
                reasoning_effort=payload.reasoning_effort,
            )
        case _:
            # An agent message and a tool call are rows the suite has no word for. It
            # asserts on what it can name, so they are left out rather than mistranslated.
            return None


@asynccontextmanager
async def open_conversation_system_under_test() -> AsyncIterator[ConversationSystemUnderTest]:
    """A real system on a temporary database, with every backend key on a scripted agent.

    Which backend key a conversation is started on changes nothing about the child: steer
    support is a fact the core reads off the contract, so a codex-keyed conversation is
    refused its steer before any child is touched and behaves exactly as the contract says
    against the same agent.
    """
    working_directory = Path(tempfile.mkdtemp(prefix="panels-conv2-"))
    socket_directory = Path(tempfile.mkdtemp(prefix="pc2-"))
    database_path = working_directory / "conversations.db"
    connection = connect(str(database_path), 5000)
    try:
        create_schema(connection)
    finally:
        connection.close()

    store = ConversationStore(str(database_path))
    subject: ConversationSystemUnderTest | None = None

    def make_child(
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _CountedChild:
        assert subject is not None
        return subject.make_child(
            resolved_start=resolved_start,
            event_sink=event_sink,
            message_files=message_files,
        )

    live_tail = ConversationLiveTail()
    message_files = ConversationMessageFiles(str(database_path))
    system = SqliteProcessConversationSystem(
        store=store,
        message_files=message_files,
        backend_child_factories={key: make_child for key in ConversationBackendKey},
        live_tail=live_tail,
    )
    subject = ConversationSystemUnderTest(system, store, socket_directory, live_tail)
    try:
        yield subject
    finally:
        await subject.close()
        shutil.rmtree(working_directory, ignore_errors=True)
        shutil.rmtree(socket_directory, ignore_errors=True)
