"""The claude bench: a scripted claude SDK client, a recording sink, and the child around them.

These were the scaffolding of the claude adapter's own unit file. They live here because
more than one test module needs them — the scripted-client bench for the obligations that
can only be provoked with a client that answers to order, and the recording sink for the
exercises that drive the claude actually installed on this machine.

They are moved here unchanged, names and all, so what a test does with them still reads the
way it always did.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    Message,
)

from planner.conversation.backends.claude_agent_sdk import (
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.backends.contracts import (
    AgentMessageCompleted,
    AgentMessageDelta,
    BackendEventValue,
    BackendPermissionAsk,
    BackendUserInputRequest,
    ComposerCatalogReported,
    ContextCompacted,
    ModelThinkingHappened,
    PermissionAskRaised,
    PlanUpdated,
    TokenUsageReported,
    ToolCallFinished,
    ToolCallProgress,
    ToolCallStarted,
    TurnEnded,
    TurnToken,
    UserInputFailed,
    UserInputRequested,
    VendorSessionCursorRebound,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.message_content import (
    MessageContent,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles


def _message_files() -> ConversationMessageFiles:
    """A file store for this exercise, under a database path of its own.

    Every adapter is handed one, because a message can carry a file and an adapter is
    what reads it. These exercises send words, so nothing is ever written here — but the
    adapter is built the way production builds it rather than with a hole where the file
    store goes.
    """
    return ConversationMessageFiles(str(Path(mkdtemp()) / "planner.db"))


CONVERSATION_ID = "c-claude-1"
SESSION_ID = "11111111-1111-4111-8111-111111111111"
ANOTHER_SESSION_ID = "22222222-2222-4222-8222-222222222222"
TURN = TurnToken(conversation_id=CONVERSATION_ID, turn_number=1)
TURN_2 = TurnToken(conversation_id=CONVERSATION_ID, turn_number=2)

REAL_CLAUDE_TESTS_ENVIRONMENT_NAME = "PANELS_REAL_CLAUDE_TESTS"
CLAUDE_EXECUTABLE = shutil.which("claude")

# The cheapest model to prove a real turn with, and one other to prove a model change.
CLAUDE_MODEL = "haiku"
CLAUDE_OTHER_MODEL = "sonnet"

real_claude_only = pytest.mark.skipif(
    os.environ.get(REAL_CLAUDE_TESTS_ENVIRONMENT_NAME) != "1"
    or CLAUDE_EXECUTABLE is None,
    reason=f"set {REAL_CLAUDE_TESTS_ENVIRONMENT_NAME}=1 with claude installed to run this",
)


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 30.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


class _ScriptedClaudeSdkClient:
    """A claude client that says what a test tells it to, and remembers what it was asked."""

    def __init__(
        self, options: ClaudeAgentOptions, *, handshake: dict[str, Any] | None = None
    ) -> None:
        self.options = options
        # What this client answers the startup handshake with, which is where the commands
        # a person may type come from. ``None`` is a child that said nothing.
        self.handshake = handshake
        self.prompts: list[str] = []
        # What a message with more than words in it was actually sent as. The SDK takes
        # either a string or a stream of user messages; this keeps the second, drained,
        # so a test can see the blocks rather than an exhausted generator.
        self.streamed_messages: list[dict[str, Any]] = []
        self.interrupts = 0
        self.watched_user_message_uuids: list[str] = []
        self.steer_admission: bool | None = True
        self.result_user_message_uuids: dict[str, frozenset[str]] = {}
        # The commands claude has finished with, as a terminal lifecycle receipt or a
        # correlated result said so. A steer claude folded into the running turn is
        # settled this way and is never named on any result.
        self.settled_user_message_uuids: set[str] = set()
        self._settlements: dict[str, asyncio.Event] = {}
        self.cancelled_user_message_uuids: frozenset[str] | None = None
        self.still_queued_user_message_uuids: frozenset[str] = frozenset()
        self.disconnected = False
        self.connect_failure: BaseException | None = None
        self.query_failure: BaseException | None = None
        self._inbox: asyncio.Queue[Message | None] = asyncio.Queue()

    async def connect(self) -> None:
        if self.connect_failure is not None:
            raise self.connect_failure

    async def get_server_info(self) -> dict[str, Any] | None:
        return self.handshake

    async def query(
        self,
        prompt: str | AsyncIterable[dict[str, Any]],
    ) -> None:
        if self.query_failure is not None:
            raise self.query_failure
        if isinstance(prompt, str):
            self.prompts.append(prompt)
            return
        async for message in prompt:
            self.streamed_messages.append(message)
            blocks = message.get("message", {}).get("content", [])
            if len(blocks) == 1 and blocks[0].get("type") == "text":
                self.prompts.append(blocks[0]["text"])

    def receive_messages(self) -> AsyncIterator[Message]:
        return self._drain()

    def watch_user_message(self, user_message_uuid: str) -> None:
        self.watched_user_message_uuids.append(user_message_uuid)

    async def wait_for_user_message_admission(
        self, user_message_uuid: str
    ) -> bool | None:
        assert user_message_uuid in self.watched_user_message_uuids
        return self.steer_admission

    def user_message_uuids_for_result(self, result_uuid: str | None) -> frozenset[str]:
        if result_uuid is None:
            return frozenset()
        named = self.result_user_message_uuids.pop(result_uuid, frozenset())
        for user_message_uuid in named:
            self.settle_user_message(user_message_uuid)
        return named

    def user_message_is_settled(self, user_message_uuid: str) -> bool:
        return user_message_uuid in self.settled_user_message_uuids

    async def wait_for_user_message_settlement(self, user_message_uuid: str) -> None:
        await self._settlement(user_message_uuid).wait()

    def settle_user_message(self, user_message_uuid: str) -> None:
        """What a terminal lifecycle receipt does, for a test to do on purpose."""
        self.settled_user_message_uuids.add(user_message_uuid)
        self._settlement(user_message_uuid).set()

    def _settlement(self, user_message_uuid: str) -> asyncio.Event:
        settlement = self._settlements.get(user_message_uuid)
        if settlement is None:
            settlement = asyncio.Event()
            self._settlements[user_message_uuid] = settlement
        return settlement

    async def _drain(self) -> AsyncIterator[Message]:
        while True:
            message = await self._inbox.get()
            try:
                if message is None:
                    return
                yield message
            finally:
                self._inbox.task_done()

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def interrupt_and_cancel_queued(
        self,
    ) -> tuple[frozenset[str], frozenset[str]]:
        self.interrupts += 1
        cancelled = self.cancelled_user_message_uuids
        if cancelled is None:
            cancelled = frozenset(self.watched_user_message_uuids)
        return cancelled, self.still_queued_user_message_uuids

    async def disconnect(self) -> None:
        self.disconnected = True

    def say(self, *messages: Message) -> None:
        for message in messages:
            self._inbox.put_nowait(message)

    def end_the_stream(self) -> None:
        self._inbox.put_nowait(None)

    async def until_taken_in(self) -> None:
        """Return once every message said so far has been worked through."""
        await self._inbox.join()


class _RecordingSink:
    """Everything the adapter told the core, in the order it told it."""

    def __init__(self) -> None:
        self.deltas: list[tuple[TurnToken, str]] = []
        self.message_contents: list[tuple[TurnToken, MessageContent]] = []
        self.tools_started: list[dict[str, Any]] = []
        self.tools_finished: list[dict[str, Any]] = []
        self.tools_progressed: list[tuple[str, str]] = []
        self.thinking_pulses: list[TurnToken] = []
        self.plans: list[list[tuple[str, str]]] = []
        self.asks: list[BackendPermissionAsk] = []
        self.user_input_requests: list[BackendUserInputRequest] = []
        self.user_input_failures: list[tuple[str, str]] = []
        self.endings: list[dict[str, Any]] = []
        self.cursors: list[str] = []
        self.composer_catalog: list[tuple[ComposerCatalogEntry, ...]] = []
        self.token_usage: list[dict[str, Any]] = []
        self.compactions: list[TurnToken] = []
        # The order the facts a result message carries were told in. What is said about a
        # turn after its ending is said about a turn that has stopped running, and is
        # dropped — so the order is the whole of whether the counts arrive at all.
        self.calls_in_order: list[str] = []
        # Set the moment a turn ends, for the exercises that drive a real claude and have
        # to wait for one rather than pumping a scripted stream themselves.
        self._turn_over = asyncio.Event()

    async def wait_for_the_turn_to_end(self) -> None:
        await self._turn_over.wait()
        self._turn_over.clear()

    async def report(self, turn: TurnToken | None, event: BackendEventValue) -> None:
        if isinstance(event, AgentMessageDelta):
            assert turn is not None
            self.deltas.append((turn, event.text_delta))
        elif isinstance(event, ModelThinkingHappened):
            assert turn is not None
            self.thinking_pulses.append(turn)
        elif isinstance(event, PlanUpdated):
            self.plans.append([(entry.text, str(entry.status)) for entry in event.entries])
        elif isinstance(event, AgentMessageCompleted):
            assert turn is not None
            self.message_contents.append((turn, event.content))
        elif isinstance(event, ToolCallStarted):
            self.tools_started.append(
                {
                    "turn": turn,
                    "tool_call_id": event.tool_call_id,
                    "title": event.title,
                    "tool_kind": event.tool_kind,
                    "detail": event.detail,
                }
            )
        elif isinstance(event, ToolCallProgress):
            self.tools_progressed.append((event.tool_call_id, event.detail))
        elif isinstance(event, ToolCallFinished):
            self.tools_finished.append(
                {
                    "turn": turn,
                    "tool_call_id": event.tool_call_id,
                    "tool_call_status": event.tool_call_status,
                    "detail": event.detail,
                }
            )
        elif isinstance(event, TokenUsageReported):
            self.calls_in_order.append("token_usage")
            self.token_usage.append(
                {
                    "turn": turn,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "cached_input_tokens": event.cached_input_tokens,
                    "cost_usd": event.cost_usd,
                }
            )
        elif isinstance(event, ContextCompacted):
            self.calls_in_order.append("context_compacted")
            assert turn is not None
            self.compactions.append(turn)
        elif isinstance(event, PermissionAskRaised):
            self.asks.append(event.ask)
        elif isinstance(event, UserInputRequested):
            self.user_input_requests.append(event.request)
        elif isinstance(event, UserInputFailed):
            self.user_input_failures.append((event.request_id, event.detail))
        elif isinstance(event, TurnEnded):
            self.calls_in_order.append("turn_ended")
            self._turn_over.set()
            self.endings.append(
                {
                    "turn": turn,
                    "ending": event.ending,
                    "error_summary": event.error_summary,
                    "standard_error_tail": event.standard_error_tail,
                }
            )
        elif isinstance(event, VendorSessionCursorRebound):
            self.cursors.append(event.vendor_session_cursor)
        elif isinstance(event, ComposerCatalogReported):
            self.composer_catalog.append(event.composer_catalog)

    @property
    def message_texts(self) -> list[tuple[TurnToken, str]]:
        """Each finished message's words. The messages themselves are above."""
        return [
            (token, message_content_text(content))
            for token, content in self.message_contents
        ]


def _start_request(
    *,
    workspace_folder: Path,
    model: str = "a-model",
    reasoning_effort: str | None = None,
    role_materials: ConversationRoleMaterials | None = None,
) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id=CONVERSATION_ID,
        backend_key=ConversationBackendKey.claude,
        model=model,
        reasoning_effort=reasoning_effort,
        role_materials=role_materials,
        workspace_folder=workspace_folder,
        access=ConversationAccess.full,
    )


def _bench(
    resolved_start: ResolvedConversationStart,
    *,
    handshake: dict[str, Any] | None = None,
) -> tuple[ClaudeAgentSdkBackendChild, _RecordingSink, list[_ScriptedClaudeSdkClient]]:
    """A child wired to a scripted client, and the clients it has been given.

    ``handshake`` is what every client this bench makes answers the startup handshake
    with. It is given here rather than set on the client afterwards because the child
    reads it while it is starting, before a test has the client in its hands.
    """
    clients: list[_ScriptedClaudeSdkClient] = []

    def make(options: ClaudeAgentOptions) -> _ScriptedClaudeSdkClient:
        client = _ScriptedClaudeSdkClient(options, handshake=handshake)
        clients.append(client)
        return client

    sink = _RecordingSink()
    factory = ClaudeAgentSdkBackendChildFactory(
        ClaudeAgentSdkChildLaunch(claude_executable=Path("/usr/bin/claude")),
        client_factory=make,
    )
    message_files = _message_files()
    child = factory(
        resolved_start=resolved_start, event_sink=sink, message_files=message_files
    )
    return child, sink, clients


async def _write(
    child: ClaudeAgentSdkBackendChild,
    text: str = "hello",
    turn_token: TurnToken = TURN,
) -> None:
    content = text_message_content(text)
    await child.write_prompt(
        turn_token,
        content,
        sender_content=content,
        sender_label="owner",
        mode=PromptDeliveryMode.queue,
        model_change=None,
        reasoning_effort_change=None,
    )


def _assistant(
    *blocks: Any, session_id: str = SESSION_ID, parent: str | None = None
) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks),
        model="claude",
        session_id=session_id,
        parent_tool_use_id=parent,
    )


def _bench_on_real_claude(
    resolved_start: ResolvedConversationStart,
) -> tuple[ClaudeAgentSdkBackendChild, _RecordingSink, None]:
    sink = _RecordingSink()
    assert CLAUDE_EXECUTABLE is not None
    factory = ClaudeAgentSdkBackendChildFactory(
        ClaudeAgentSdkChildLaunch(claude_executable=Path(CLAUDE_EXECUTABLE))
    )
    return (
        factory(
            resolved_start=resolved_start,
            event_sink=sink,
            message_files=_message_files(),
        ),
        sink,
        None,
    )


async def _until_the_turn_ends(sink: _RecordingSink, *, seconds: float = 180.0) -> None:
    ended = len(sink.endings) + 1

    async def wait() -> None:
        while len(sink.endings) < ended:
            await asyncio.sleep(0.1)

    await asyncio.wait_for(wait(), seconds)
