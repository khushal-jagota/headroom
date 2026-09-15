"""What the claude adapter has to be true about, close to where it does it.

The conformance suite proves the contract from outside. These are the obligations no
outside observer can see: that a fresh session is started under an id of our own before
anything can arrive under it, that a resume which did not restore is never quietly replaced
by a fresh thread, that a hook message's passing session id never becomes the conversation's
cursor, that thinking is dropped where it arrives, and that a model change is refused to a
child that cannot make it rather than half-made.

Everything here runs against a scripted SDK client. The last few tests talk to the claude
actually installed on this machine and cost real model calls, so they are opt-in: set
``PANELS_REAL_CLAUDE_TESTS=1`` to run them.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from base64 import b64encode
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, cast

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    CLINotFoundError,
    HookEventMessage,
    Message,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    Transport,
    UserMessage,
)

from planner.conversation.backends.claude_agent_sdk import (
    ALWAYS_ALLOW_THIS_SESSION_OPTION_ID,
    APPROVE_ONCE_OPTION_ID,
    CLAUDE_SDK_MAX_BUFFER_SIZE,
    DECLINE_OPTION_ID,
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
    _ClaudeProtocolTransport,
    claude_sdk_client,
)
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendSteerAccepted,
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
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    ToolCallStatus,
    UserInputAnswer,
)
from planner.conversation.message_content import (
    MessageContent,
    MessageFile,
    MessageImage,
    MessageText,
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


# --- the scripted client and the recording sink --------------------------------------------


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

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        if self.query_failure is not None:
            raise self.query_failure
        if isinstance(prompt, str):
            self.prompts.append(prompt)
            return
        async for message in prompt:
            self.streamed_messages.append(message)

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
        return self.result_user_message_uuids.pop(result_uuid, frozenset())

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


class _ScriptedRawTransport(Transport):
    def __init__(self) -> None:
        self.writes: list[str] = []
        self._inbox: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def connect(self) -> None:
        return None

    async def write(self, data: str) -> None:
        self.writes.append(data)

    def read_messages(self) -> AsyncIterator[dict[str, Any]]:
        return self._read_messages()

    async def _read_messages(self) -> AsyncIterator[dict[str, Any]]:
        while (message := await self._inbox.get()) is not None:
            yield message

    async def close(self) -> None:
        self._inbox.put_nowait(None)

    def is_ready(self) -> bool:
        return True

    async def end_input(self) -> None:
        return None

    def say(self, message: dict[str, Any]) -> None:
        self._inbox.put_nowait(message)


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

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        self.deltas.append((turn_token, text_delta))

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        self.thinking_pulses.append(turn_token)

    async def plan_updated(self, turn_token: TurnToken, entries: Any) -> None:
        self.plans.append([(entry.text, str(entry.status)) for entry in entries])

    @property
    def message_texts(self) -> list[tuple[TurnToken, str]]:
        """Each finished message's words. The messages themselves are above."""
        return [
            (token, message_content_text(content))
            for token, content in self.message_contents
        ]

    async def agent_message_completed(
        self, turn_token: TurnToken, content: MessageContent
    ) -> None:
        self.message_contents.append((turn_token, content))

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        self.tools_started.append(
            {
                "turn": turn_token,
                "tool_call_id": tool_call_id,
                "title": title,
                "tool_kind": tool_kind,
                "detail": detail,
            }
        )

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        self.tools_progressed.append((tool_call_id, detail))

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        self.tools_finished.append(
            {
                "turn": turn_token,
                "tool_call_id": tool_call_id,
                "tool_call_status": tool_call_status,
                "detail": detail,
            }
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
        self.calls_in_order.append("token_usage")
        self.token_usage.append(
            {
                "turn": turn_token,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cached_input_tokens": cached_input_tokens,
                "cost_usd": cost_usd,
            }
        )

    async def context_compacted(self, turn_token: TurnToken) -> None:
        self.calls_in_order.append("context_compacted")
        self.compactions.append(turn_token)

    async def permission_ask_raised(
        self, turn_token: TurnToken, ask: BackendPermissionAsk
    ) -> None:
        del turn_token
        self.asks.append(ask)

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        del turn_token
        self.user_input_requests.append(request)

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        del turn_token
        self.user_input_failures.append((request_id, detail))

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        self.calls_in_order.append("turn_ended")
        self._turn_over.set()
        self.endings.append(
            {
                "turn": turn_token,
                "ending": ending,
                "error_summary": error_summary,
                "standard_error_tail": standard_error_tail,
            }
        )

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        self.cursors.append(vendor_session_cursor)

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        self.composer_catalog.append(composer_catalog)


def _command(
    name: str, description: str, argument_hint: str | None = None
) -> ComposerCatalogEntry:
    return ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.command,
        display_text=f"/{name}",
        insertion_text=f"/{name} ",
        description=description,
        argument_hint=argument_hint,
    )


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
    _BENCH_MESSAGE_FILES[id(child)] = message_files
    return child, sink, clients


# Which file store each bench built its child with, so an exercise can keep a file where
# the adapter will look for it.
_BENCH_MESSAGE_FILES: dict[int, ConversationMessageFiles] = {}


def _bench_message_files(child: ClaudeAgentSdkBackendChild) -> ConversationMessageFiles:
    return _BENCH_MESSAGE_FILES[id(child)]


async def _connected_bench(
    workspace: Path,
) -> tuple[ClaudeAgentSdkBackendChild, _RecordingSink, list[_ScriptedClaudeSdkClient]]:
    """A bench whose child is up and whose session is bound, ready to be written to."""
    resolved_start = _start_request(workspace_folder=workspace)
    child, sink, clients = _bench(resolved_start)
    await child.start(resolved_start, vendor_session_cursor=None)
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
        mode=PromptDeliveryMode.run_when_free,
        model_change=None,
        reasoning_effort_change=None,
    )


async def _cancel_with_result(
    child: ClaudeAgentSdkBackendChild,
    client: _ScriptedClaudeSdkClient,
    result: ResultMessage,
) -> None:
    cancelling = asyncio.create_task(child.cancel_running_turn())
    await asyncio.sleep(0)
    client.say(result)
    await cancelling
    await client.until_taken_in()


def _result(
    *,
    session_id: str = SESSION_ID,
    subtype: str = "success",
    is_error: bool = False,
    terminal_reason: str | None = "completed",
    errors: list[str] | None = None,
    usage: dict[str, Any] | None = None,
    total_cost_usd: float | None = None,
    result_uuid: str | None = None,
) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        terminal_reason=terminal_reason,
        errors=errors,
        usage=usage,
        total_cost_usd=total_cost_usd,
        uuid=result_uuid,
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


# --- the seam ---------------------------------------------------------------------------------


def test_the_custom_transport_retains_uuid_admission_and_result_membership() -> None:
    async def exercise() -> None:
        inner = _ScriptedRawTransport()
        transport = _ClaudeProtocolTransport(inner)
        command_uuid = "30000000-0000-4000-8000-00000000000c"
        transport.watch_user_message(command_uuid)
        messages = cast(AsyncGenerator[dict[str, Any], None], transport.read_messages())

        first = asyncio.ensure_future(anext(messages))
        inner.say(
            {
                "type": "command_lifecycle",
                "command_uuid": command_uuid,
                "state": "queued",
            }
        )
        assert await first == {
            "type": "command_lifecycle",
            "command_uuid": command_uuid,
            "state": "queued",
        }
        assert await transport.wait_for_user_message_admission(command_uuid) is True

        second = asyncio.ensure_future(anext(messages))
        inner.say(
            {
                "type": "command_lifecycle",
                "command_uuid": command_uuid,
                "state": "completed",
            }
        )
        await second
        assert transport.user_message_uuids_for_result("root-result") == frozenset()

        third = asyncio.ensure_future(anext(messages))
        inner.say(
            {
                "type": "result",
                "uuid": "result-1",
                "user_message_uuid": command_uuid,
                "user_message_uuids": [command_uuid, "another-command"],
            }
        )
        await third
        assert transport.user_message_uuids_for_result("result-1") == frozenset(
            {command_uuid, "another-command"}
        )
        await messages.aclose()

    _run(exercise)


def test_the_custom_transport_routes_permission_callbacks_over_stdio() -> None:
    async def permit(
        tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        del tool_name, tool_input, context
        return PermissionResultDeny(message="no")

    client = claude_sdk_client(
        ClaudeAgentOptions(
            cli_path=Path("/usr/bin/claude"),
            can_use_tool=permit,
        )
    )
    observed = client._transport  # type: ignore[attr-defined]
    inner = observed._inner
    command = inner._build_command()

    assert command[command.index("--permission-prompt-tool") + 1] == "stdio"
    assert client._client.options.can_use_tool is permit  # type: ignore[attr-defined]


def test_the_custom_transport_requests_cancel_queued_and_reads_its_receipt() -> None:
    async def exercise() -> None:
        inner = _ScriptedRawTransport()
        transport = _ClaudeProtocolTransport(inner)
        messages = cast(AsyncGenerator[dict[str, Any], None], transport.read_messages())
        cancelling = asyncio.create_task(transport.interrupt_and_cancel_queued())
        await asyncio.sleep(0)
        request = json.loads(inner.writes[-1])
        assert request["request"] == {"subtype": "interrupt", "cancel_queued": True}

        receipt = asyncio.ensure_future(anext(messages))
        inner.say(
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": request["request_id"],
                    "response": {
                        "cancelled": ["queued-command"],
                        "still_queued": [],
                    },
                },
            }
        )
        await receipt
        assert await cancelling == (frozenset({"queued-command"}), frozenset())
        await messages.aclose()

    _run(exercise)


def test_the_adapter_is_the_seam_the_core_talks_to(tmp_path: Path) -> None:
    """Production does not wire claude in yet, so the shape is held to here instead.

    The annotations are the assertion: a child or a factory that drifted from the seam is a
    type error rather than something found when a conversation is first started on claude.
    """
    child, _, _ = _bench(_start_request(workspace_folder=tmp_path))
    backend_child: BackendChild = child
    backend_child_factory: BackendChildFactory = ClaudeAgentSdkBackendChildFactory(
        ClaudeAgentSdkChildLaunch()
    )
    assert backend_child is child
    assert backend_child_factory is not None


# --- the session this conversation resumes from ---------------------------------------------


def test_a_fresh_session_is_started_under_an_id_of_our_own(tmp_path: Path) -> None:
    """The cursor is minted here and reported before the first message can arrive under it.

    Letting claude mint it would leave a window in which the conversation has a live session
    it cannot name, and a child stopped in that window could never be resumed.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        options = clients[0].options
        assert options.session_id is not None
        assert options.resume is None
        # Reported before start returned, and it is the very id claude was told to use.
        assert sink.cursors == [options.session_id]
        await child.stop()

    _run(exercise)


def test_a_resume_names_the_session_it_wants_and_mints_nothing(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=SESSION_ID
        )
        assert clients[0].options.resume == SESSION_ID
        assert clients[0].options.session_id is None
        # Nothing was rebound: the conversation already knows the session it is in.
        assert sink.cursors == []
        await child.stop()

    _run(exercise)


def test_fresh_and_resumed_sessions_use_the_explicit_message_buffer_limit(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path)
        child, _, clients = _bench(resolved_start)

        await child.start(resolved_start, vendor_session_cursor=None)
        assert clients[0].options.max_buffer_size == CLAUDE_SDK_MAX_BUFFER_SIZE
        await child.stop()

        await child.start(resolved_start, vendor_session_cursor=SESSION_ID)
        assert clients[1].options.max_buffer_size == CLAUDE_SDK_MAX_BUFFER_SIZE
        await child.stop()

    _run(exercise)


def test_a_session_that_will_not_load_is_never_replaced_by_a_fresh_one(
    tmp_path: Path,
) -> None:
    """Claude refuses to come up at all when it does not have the session that was named.

    That refusal is a session that did not load, not a process that would not spawn, and
    what claude said about it travels with the failure.
    """

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path)
        child, _, _ = _bench_that_will_not_connect(
            resolved_start,
            RuntimeError("Command failed with exit code 1"),
            standard_error=f"No conversation found with session ID: {SESSION_ID}",
        )
        with pytest.raises(SessionLoadFailed) as would_not_load:
            await child.start(resolved_start, vendor_session_cursor=SESSION_ID)
        # What claude said about it travels with the failure, so the reason is readable.
        assert "No conversation found with session ID" in str(would_not_load.value)

    _run(exercise)


def test_a_child_that_will_not_spawn_says_so_rather_than_blaming_the_session(
    tmp_path: Path,
) -> None:
    """A fresh start has no session to fail to load, so its failure is a spawn failure."""

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path)
        child, _, _ = _bench_that_will_not_connect(
            resolved_start, RuntimeError("claude fell over")
        )
        with pytest.raises(BackendSpawnFailed):
            await child.start(resolved_start, vendor_session_cursor=None)

    _run(exercise)


def test_a_missing_executable_is_a_spawn_failure_even_under_a_cursor(
    tmp_path: Path,
) -> None:
    """There being no claude to run is not this conversation's session's fault."""

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path)
        child, _, _ = _bench_that_will_not_connect(
            resolved_start, CLINotFoundError("Claude Code not found")
        )
        with pytest.raises(BackendSpawnFailed):
            await child.start(resolved_start, vendor_session_cursor=SESSION_ID)

    _run(exercise)


def _bench_that_will_not_connect(
    resolved_start: ResolvedConversationStart,
    failure: BaseException,
    *,
    standard_error: str | None = None,
) -> tuple[ClaudeAgentSdkBackendChild, _RecordingSink, list[_ScriptedClaudeSdkClient]]:
    """A child whose client refuses to come up, the way a dead CLI's does."""
    clients: list[_ScriptedClaudeSdkClient] = []

    def make(options: ClaudeAgentOptions) -> _ScriptedClaudeSdkClient:
        client = _ScriptedClaudeSdkClient(options)
        client.connect_failure = failure
        if standard_error is not None and options.stderr is not None:
            # The CLI writes its complaint before it goes, so the adapter has it to carry.
            options.stderr(standard_error)
        clients.append(client)
        return client

    sink = _RecordingSink()
    factory = ClaudeAgentSdkBackendChildFactory(
        ClaudeAgentSdkChildLaunch(claude_executable=Path("/usr/bin/claude")),
        client_factory=make,
    )
    return (
        factory(
            resolved_start=resolved_start,
            event_sink=sink,
            message_files=_message_files(),
        ),
        sink,
        clients,
    )


def test_a_resume_that_answers_under_another_session_is_refused(tmp_path: Path) -> None:
    """A fresh thread standing in for this conversation's own is never accepted in silence.

    The cursor is left alone — writing the substitute down is exactly the silent acceptance
    this guards against — and the turn is failed saying what happened.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=SESSION_ID
        )
        await _write(child)
        clients[0].say(_assistant(TextBlock(text="hi"), session_id=ANOTHER_SESSION_ID))
        await clients[0].until_taken_in()

        assert sink.cursors == []
        assert len(sink.endings) == 1
        assert sink.endings[0]["ending"] is ConversationTurnEnding.failed
        assert ANOTHER_SESSION_ID in str(sink.endings[0]["error_summary"])
        assert sink.message_texts == []
        # Nothing more is written to a child that is not this conversation's session.
        # The stored cursor remains safe to try on one replacement child.
        with pytest.raises(NeedsRebind):
            await _write(child, "again")
        await child.stop()

    _run(exercise)


def test_a_hook_messages_session_id_never_becomes_the_cursor(tmp_path: Path) -> None:
    """Hook messages carry the id of the hook's own run, which is not the conversation's."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=SESSION_ID
        )
        await _write(child)
        clients[0].say(
            HookEventMessage(
                subtype="hook_started",
                data={"session_id": ANOTHER_SESSION_ID},
                hook_event_name="SessionStart",
                session_id=ANOTHER_SESSION_ID,
            ),
            SystemMessage(
                subtype="hook_progress", data={"session_id": ANOTHER_SESSION_ID}
            ),
            _assistant(TextBlock(text="hi")),
            _result(),
        )
        await clients[0].until_taken_in()

        assert sink.cursors == []
        assert sink.endings[0]["ending"] is ConversationTurnEnding.completed
        await child.stop()

    _run(exercise)


def test_a_confirmed_session_that_moves_is_followed(tmp_path: Path) -> None:
    """Once the resume has answered under its own id, a later change is claude moving it."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=SESSION_ID
        )
        await _write(child)
        clients[0].say(
            _assistant(TextBlock(text="hi")),
            _assistant(TextBlock(text="still here"), session_id=ANOTHER_SESSION_ID),
        )
        await clients[0].until_taken_in()

        assert sink.cursors == [ANOTHER_SESSION_ID]
        assert sink.endings == []
        await child.stop()

    _run(exercise)


# --- the commands a person may type at this child ---------------------------------------------


def test_the_commands_claude_takes_are_reported_as_the_session_is_established(
    tmp_path: Path,
) -> None:
    """They come off the startup handshake, so the menu is right before anyone can type.

    Each is reported under the names Panels knows commands by, which for the argument hint
    is not the name claude puts it on the wire under.
    """

    async def exercise() -> None:
        child, sink, _ = _bench(
            _start_request(workspace_folder=tmp_path),
            handshake={
                "commands": [
                    {
                        "name": "review",
                        "description": "Review the working tree",
                        "argumentHint": "[path]",
                    },
                    {"name": "clear", "description": "Start the conversation again"},
                ]
            },
        )
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )

        assert sink.composer_catalog == [
            (
                _command(
                    name="review",
                    description="Review the working tree",
                    argument_hint="[path]",
                ),
                # Nothing to type after it, so there is no hint rather than an empty one.
                _command(name="clear", description="Start the conversation again"),
            )
        ]
        await child.stop()

    _run(exercise)


def test_an_ordinary_catalog_command_keeps_exact_native_dispatch_text(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, _, clients = _bench(
            _start_request(workspace_folder=tmp_path),
            handshake={
                "commands": [
                    {"name": "review", "description": "Review the working tree"}
                ]
            },
        )
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        sender_content = text_message_content("/review focus on tests")

        await child.write_prompt(
            TURN,
            text_message_content("You are the worker.\n\n/review focus on tests"),
            sender_content=sender_content,
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            model_change=None,
            reasoning_effort_change=None,
        )

        assert clients[0].prompts == ["/review focus on tests"]
        await child.stop()

    _run(exercise)


def test_a_steered_catalog_command_keeps_exact_native_dispatch_text(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, _, clients = _bench(
            _start_request(workspace_folder=tmp_path),
            handshake={
                "commands": [
                    {"name": "review", "description": "Review the working tree"}
                ]
            },
        )
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child, "start")

        outcome = await child.steer(
            TURN,
            text_message_content("/review focus on tests"),
            sender_label="owner",
        )

        assert isinstance(outcome, BackendSteerAccepted)
        assert clients[0].streamed_messages[-1]["message"]["content"] == [
            {"type": "text", "text": "/review focus on tests"}
        ]
        await child.stop()

    _run(exercise)


def test_the_commands_are_the_answer_for_this_conversations_own_folder(
    tmp_path: Path,
) -> None:
    """The child that answered about commands is the one running where the work happens.

    Claude's list is not the same everywhere: a project keeps commands of its own in the
    folder, and the CLI only reports them when it was started there. So the two facts have
    to be one fact — the client the commands were read off must be the client that was
    given this conversation's workspace folder. Reading them from any other claude, a
    machine-wide probe included, would answer about somewhere nobody is working.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(
            _start_request(workspace_folder=tmp_path),
            handshake={
                "commands": [{"name": "ship", "description": "This project's own"}]
            },
        )
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )

        assert len(clients) == 1
        assert clients[0].options.cwd == str(tmp_path)
        assert sink.composer_catalog == [
            (_command(name="ship", description="This project's own"),)
        ]
        await child.stop()

    _run(exercise)


def test_a_commands_other_spellings_are_dropped_rather_than_offered(
    tmp_path: Path,
) -> None:
    """Claude reports the aliases a command also answers to. Panels offers the one name."""

    async def exercise() -> None:
        child, sink, _ = _bench(
            _start_request(workspace_folder=tmp_path),
            handshake={
                "commands": [
                    {
                        "name": "review",
                        "description": "Review the working tree",
                        "aliases": ["r", "rv"],
                    }
                ]
            },
        )
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )

        assert sink.composer_catalog == [
            (_command(name="review", description="Review the working tree"),)
        ]
        await child.stop()

    _run(exercise)


def test_an_entry_with_no_name_to_type_is_left_out_and_the_rest_stand(
    tmp_path: Path,
) -> None:
    """The handshake is claude's, so a shape this does not recognise is skipped, not read.

    A command nobody could type is no use in a menu, and it is no reason to refuse the
    session either: the conversation starts and the commands that did make sense are
    offered.
    """

    async def exercise() -> None:
        child, sink, _ = _bench(
            _start_request(workspace_folder=tmp_path),
            handshake={
                "commands": [
                    "not a command at all",
                    {"description": "no name to type"},
                    {"name": 12, "description": "a name that is not words"},
                    {"name": "review", "description": "Review the working tree"},
                ]
            },
        )
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )

        assert sink.composer_catalog == [
            (_command(name="review", description="Review the working tree"),)
        ]
        assert sink.cursors != []
        await child.stop()

    _run(exercise)


def test_a_handshake_that_says_nothing_about_commands_reports_nothing(
    tmp_path: Path,
) -> None:
    """A child with nothing to say about commands is a session that starts all the same."""

    async def exercise() -> None:
        for handshake in (None, {"output_style": "default"}):
            child, sink, _ = _bench(
                _start_request(workspace_folder=tmp_path), handshake=handshake
            )
            await child.start(
                _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
            )

            assert sink.composer_catalog == []
            assert sink.cursors != []
            await child.stop()

    _run(exercise)


def test_claude_saying_it_has_no_commands_is_not_the_same_as_saying_nothing(
    tmp_path: Path,
) -> None:
    """An empty list is an answer — this child takes no commands — so it is reported."""

    async def exercise() -> None:
        child, sink, _ = _bench(
            _start_request(workspace_folder=tmp_path), handshake={"commands": []}
        )
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )

        assert sink.composer_catalog == [()]
        await child.stop()

    _run(exercise)


# --- the values the child runs on -----------------------------------------------------------


def test_the_start_requests_values_reach_the_options(tmp_path: Path) -> None:
    """Workspace folder, model, effort, access posture and identity variables, all passed on.

    HOME is not among them, and never is: overriding it moves the login keychain the CLI
    reads its credentials from, and the CLI then reports itself logged out.
    """

    async def exercise() -> None:
        resolved_start = _start_request(
            workspace_folder=tmp_path,
            model="claude-haiku-4-5",
            reasoning_effort="low",
            role_materials=ConversationRoleMaterials(
                role_text="You are the worker.",
                identity_environment_variables=(("PANELS_IDENTITY_TICKET_ID", "t-1"),),
            ),
        )
        child, _, clients = _bench(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        options = clients[0].options

        assert options.cwd == str(tmp_path)
        assert options.model == "claude-haiku-4-5"
        assert options.effort == "low"
        assert options.permission_mode == "bypassPermissions"
        assert options.env == {"PANELS_IDENTITY_TICKET_ID": "t-1"}
        assert "HOME" not in options.env
        assert options.include_partial_messages is True
        assert options.can_use_tool is not None
        assert options.cli_path == Path("/usr/bin/claude")
        # Without this the SDK sends an empty system prompt, and the child is a bare model
        # rather than a coding agent.
        assert options.system_prompt == {"type": "preset", "preset": "claude_code"}
        await child.stop()

    _run(exercise)


def test_a_reasoning_effort_claude_does_not_have_is_a_session_that_did_not_load(
    tmp_path: Path,
) -> None:
    """A session that cannot be put on the value the conversation runs on is not one to
    write under: the text would go to an agent nobody configured."""

    async def exercise() -> None:
        resolved_start = _start_request(
            workspace_folder=tmp_path, reasoning_effort="ludicrous"
        )
        child, _, _ = _bench(resolved_start)
        with pytest.raises(SessionLoadFailed):
            await child.start(resolved_start, vendor_session_cursor=None)

    _run(exercise)


def test_a_model_change_asks_for_a_child_started_on_it(tmp_path: Path) -> None:
    """Claude takes its model when the child starts, so a change is a rebind, not a write.

    It is raised before anything reaches the wire, which is what keeps the change and the
    prompt one act: nothing is half-made.
    """

    async def exercise() -> None:
        resolved_start = _start_request(
            workspace_folder=tmp_path, model="claude-haiku-4-5"
        )
        child, _, clients = _bench(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        with pytest.raises(NeedsRebind):
            content = text_message_content("on the other model please")
            await child.write_prompt(
                TURN,
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change="claude-sonnet-4-5",
                reasoning_effort_change=None,
            )
        assert clients[0].prompts == []
        await child.stop()

    _run(exercise)


def test_a_reasoning_effort_change_asks_for_a_child_started_on_it(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(
            workspace_folder=tmp_path, reasoning_effort="low"
        )
        child, _, clients = _bench(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        with pytest.raises(NeedsRebind):
            content = text_message_content("think harder")
            await child.write_prompt(
                TURN,
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=None,
                reasoning_effort_change="high",
            )
        assert clients[0].prompts == []
        await child.stop()

    _run(exercise)


def test_the_rebound_child_takes_the_prompt_that_asked_for_it(tmp_path: Path) -> None:
    """The change is already in force on the new child, so the re-delivery is a plain write.

    Without this the core's rebind would ask for another rebind for ever, and a carried
    change could never land.
    """

    async def exercise() -> None:
        # The core starts the new child on the values the delivery was carrying.
        resolved_start = _start_request(
            workspace_folder=tmp_path,
            model="claude-sonnet-4-5",
            reasoning_effort="high",
        )
        child, _, clients = _bench(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=SESSION_ID)
        content = text_message_content("on the other model please")
        await child.write_prompt(
            TURN,
            content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            model_change="claude-sonnet-4-5",
            reasoning_effort_change="high",
        )
        assert clients[0].prompts == ["owner:\non the other model please"]
        await child.stop()

    _run(exercise)


def test_the_label_is_in_the_prompt_and_the_mode_is_dropped(tmp_path: Path) -> None:
    """The SDK's wire carries the sender label inside the user message.

    The delivery mode still has no SDK channel, so it does not alter the wire message.
    """

    async def exercise() -> None:
        child, _, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        content = text_message_content("hello")
        await child.write_prompt(
            TURN,
            content,
            sender_content=content,
            sender_label="the automatic loop",
            mode=PromptDeliveryMode.send_now,
            model_change=None,
            reasoning_effort_change=None,
        )
        assert clients[0].prompts == ["the automatic loop:\nhello"]
        await child.stop()

    _run(exercise)


def test_steering_requires_the_exact_running_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, _, _ = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        refused = await child.steer(
            TurnToken("c", 1), text_message_content("go left"), sender_label="owner"
        )
        assert isinstance(refused, BackendSteerRefused)
        await child.stop()

    _run(exercise)


def test_steering_uses_one_uuid_and_accepts_a_provider_queue_receipt(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, _, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)

        outcome = await child.steer(
            TURN, text_message_content("go left"), sender_label="owner"
        )

        assert isinstance(outcome, BackendSteerAccepted)
        assert len(clients[0].watched_user_message_uuids) == 1
        sent = clients[0].streamed_messages[-1]
        assert sent["uuid"] == clients[0].watched_user_message_uuids[0]
        assert sent["message"]["content"] == [
            {"type": "text", "text": "owner:\ngo left"}
        ]
        await child.stop()

    _run(exercise)


def test_steering_keeps_rich_content_under_its_owned_uuid(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, _, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        files = _bench_message_files(child)
        image = await files.keep(
            CONVERSATION_ID, b"image bytes", media_type="image/png"
        )
        document = await files.keep(
            CONVERSATION_ID, b"document", media_type="text/plain"
        )

        outcome = await child.steer(
            TURN,
            (
                MessageText(text="read both"),
                MessageImage(
                    stored_file_id=image.stored_file_id, media_type="image/png"
                ),
                MessageFile(
                    stored_file_id=document.stored_file_id,
                    file_name="note.txt",
                    media_type="text/plain",
                    byte_count=8,
                ),
            ),
            sender_label="owner",
        )

        assert isinstance(outcome, BackendSteerAccepted)
        sent = clients[0].streamed_messages[-1]
        assert sent["uuid"] == clients[0].watched_user_message_uuids[-1]
        assert sent["message"]["content"][0] == {
            "type": "text",
            "text": "owner:\nread both",
        }
        assert sent["message"]["content"][1]["source"]["data"] == b64encode(
            b"image bytes"
        ).decode("ascii")
        assert (
            str(document.absolute_path.resolve())
            in sent["message"]["content"][2]["text"]
        )
        await child.stop()

    _run(exercise)


def test_a_missing_steer_attachment_refuses_without_retained_ownership(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)

        outcome = await child.steer(
            TURN,
            (MessageImage(stored_file_id="missing-image", media_type="image/png"),),
            sender_label="owner",
        )

        assert isinstance(outcome, BackendSteerRefused)
        assert clients[0].watched_user_message_uuids == []
        assert clients[0].streamed_messages == []
        clients[0].say(_result(result_uuid="root-result"))
        await clients[0].until_taken_in()
        assert len(sink.endings) == 1
        await child.stop()

    _run(exercise)


def test_stop_during_steer_content_preparation_prevents_the_later_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        preparation_started = asyncio.Event()
        release_preparation = asyncio.Event()

        async def paused_encoding(stored_file_id: str) -> str:
            del stored_file_id
            preparation_started.set()
            await release_preparation.wait()
            return "encoded"

        monkeypatch.setattr(child, "_encoded_bytes", paused_encoding)
        steering = asyncio.create_task(
            child.steer(
                TURN,
                (MessageImage(stored_file_id="image", media_type="image/png"),),
                sender_label="owner",
            )
        )
        await preparation_started.wait()
        cancelling = asyncio.create_task(child.cancel_running_turn())
        await asyncio.sleep(0)
        clients[0].say(
            _result(terminal_reason="aborted_streaming", result_uuid="stopped")
        )
        await cancelling
        await clients[0].until_taken_in()
        release_preparation.set()

        outcome = await steering
        assert isinstance(outcome, BackendSteerRefused)
        assert clients[0].watched_user_message_uuids == []
        assert clients[0].streamed_messages == []
        assert sink.endings[0]["ending"] is ConversationTurnEnding.interrupted
        await child.stop()

    _run(exercise)


def test_an_unconfirmed_steer_is_uncertain_and_still_owns_its_later_result(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        clients[0].steer_admission = None
        outcome = await child.steer(
            TURN, text_message_content("go left"), sender_label="owner"
        )
        steer_uuid = clients[0].watched_user_message_uuids[0]
        assert isinstance(outcome, BackendSteerUncertain)

        clients[0].say(_result(result_uuid="root-result"))
        await clients[0].until_taken_in()
        assert sink.endings == []

        clients[0].result_user_message_uuids["steer-result"] = frozenset({steer_uuid})
        clients[0].say(
            _assistant(TextBlock(text="owned continuation")),
            _result(result_uuid="steer-result"),
        )
        await clients[0].until_taken_in()
        assert sink.message_texts[-1] == (TURN, "owned continuation")
        assert len(sink.token_usage) == 2
        assert len(sink.endings) == 1
        await child.stop()

    _run(exercise)


def test_a_steer_write_failure_is_uncertain_and_keeps_later_correlated_work(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        clients[0].query_failure = BrokenPipeError("uncertain write")

        outcome = await child.steer(
            TURN, text_message_content("go left"), sender_label="owner"
        )
        steer_uuid = clients[0].watched_user_message_uuids[0]
        assert isinstance(outcome, BackendSteerUncertain)

        clients[0].say(_result(result_uuid="root-result"))
        await clients[0].until_taken_in()
        assert sink.endings == []
        clients[0].result_user_message_uuids["steer-result"] = frozenset({steer_uuid})
        clients[0].say(
            _assistant(TextBlock(text="late after uncertain write")),
            _result(result_uuid="steer-result"),
        )
        await clients[0].until_taken_in()
        assert sink.message_texts[-1] == (TURN, "late after uncertain write")
        assert len(sink.endings) == 1
        await child.stop()

    _run(exercise)


def test_multiple_steers_settle_under_one_turn_after_one_correlated_result(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        assert isinstance(
            await child.steer(
                TURN, text_message_content("first"), sender_label="owner"
            ),
            BackendSteerAccepted,
        )
        assert isinstance(
            await child.steer(
                TURN, text_message_content("second"), sender_label="owner"
            ),
            BackendSteerAccepted,
        )
        clients[0].say(
            _result(
                result_uuid="root-result",
                usage={"input_tokens": 1, "output_tokens": 2},
            )
        )
        await clients[0].until_taken_in()
        assert sink.endings == []
        assert len(sink.token_usage) == 1

        clients[0].result_user_message_uuids["steer-result"] = frozenset(
            clients[0].watched_user_message_uuids
        )
        clients[0].say(
            _assistant(TextBlock(text="late owned result")),
            _result(
                result_uuid="steer-result",
                usage={"input_tokens": 3, "output_tokens": 4},
            ),
        )
        await clients[0].until_taken_in()
        assert len(sink.endings) == 1
        assert len(sink.token_usage) == 2
        assert sink.message_texts[-1] == (TURN, "late owned result")

        await _write(child, "ordinary queue successor", TURN_2)
        assert clients[0].prompts[-1] == "owner:\nordinary queue successor"
        clients[0].say(_result(result_uuid="next-result"))
        await clients[0].until_taken_in()
        assert [ending["turn"] for ending in sink.endings] == [TURN, TURN_2]
        await child.stop()

    _run(exercise)


def test_a_prompt_that_does_not_reach_the_wire_says_so(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, _, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        clients[0].query_failure = BrokenPipeError("the child has gone")
        with pytest.raises(PromptWriteFailed):
            await _write(child)
        # The failed current write is never retried. A later prompt can safely replace the
        # child because the adapter refuses it before another query starts.
        clients[0].query_failure = None
        with pytest.raises(NeedsRebind):
            await _write(child)
        assert clients[0].prompts == []
        await child.stop()

    _run(exercise)


# --- what the agent says ---------------------------------------------------------------------


def test_thinking_is_dropped_where_it_arrives_and_only_its_arrival_is_told(
    tmp_path: Path,
) -> None:
    """Not stored and not shown, in either of the two places claude sends it.

    What is passed on is that it happened — twice, once for each place — with not a word
    of what was thought going anywhere.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            StreamEvent(
                uuid="u-1",
                session_id=session_id,
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "thinking_delta", "thinking": "hmm"},
                },
            ),
            _assistant(
                ThinkingBlock(thinking="hmm", signature="s"),
                TextBlock(text="the answer"),
                session_id=session_id,
            ),
        )
        await clients[0].until_taken_in()

        assert sink.deltas == []
        assert sink.message_texts == [(TURN, "the answer")]
        # Both places claude sends thinking said so, and neither carried the thought.
        assert sink.thinking_pulses == [TURN, TURN]
        assert "hmm" not in repr(sink.__dict__)
        await child.stop()

    _run(exercise)


def test_streamed_text_is_shown_and_the_finished_message_is_the_row(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        for piece in ("Hel", "lo"):
            clients[0].say(
                StreamEvent(
                    uuid="u",
                    session_id=session_id,
                    event={
                        "type": "content_block_delta",
                        "delta": {"type": "text_delta", "text": piece},
                    },
                )
            )
        clients[0].say(_assistant(TextBlock(text="Hello"), session_id=session_id))
        await clients[0].until_taken_in()

        assert [text for _, text in sink.deltas] == ["Hel", "lo"]
        assert sink.message_texts == [(TURN, "Hello")]
        await child.stop()

    _run(exercise)


def test_a_message_around_a_tool_call_is_recorded_in_the_order_it_happened(
    tmp_path: Path,
) -> None:
    """Said something, worked, said more — three things, in that order, not one run-on row."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _assistant(
                TextBlock(text="Let me look."),
                ToolUseBlock(id="tool-1", name="Bash", input={"command": "ls"}),
                TextBlock(text="Done."),
                session_id=session_id,
            ),
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tool-1", content="a.txt", is_error=False
                    )
                ]
            ),
        )
        await clients[0].until_taken_in()

        assert [text for _, text in sink.message_texts] == ["Let me look.", "Done."]
        assert sink.tools_started == [
            {
                "turn": TURN,
                "tool_call_id": "tool-1",
                "title": "Bash",
                "tool_kind": "Bash",
                "detail": '{"command": "ls"}',
            }
        ]
        assert sink.tools_finished == [
            {
                "turn": TURN,
                "tool_call_id": "tool-1",
                "tool_call_status": ToolCallStatus.completed,
                "detail": "a.txt",
            }
        ]
        await child.stop()

    _run(exercise)


def test_a_valid_tool_result_just_over_one_megabyte_reaches_panels(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)

        result = "x" * (1024 * 1024 + 1)
        clients[0].say(
            _assistant(
                ToolUseBlock(id="tool-1", name="Read", input={}), session_id=session_id
            ),
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tool-1", content=result, is_error=False
                    )
                ]
            ),
        )
        await clients[0].until_taken_in()

        assert sink.tools_finished[0]["tool_call_id"] == "tool-1"
        assert sink.tools_finished[0]["tool_call_status"] is ToolCallStatus.completed
        assert sink.tools_finished[0]["detail"] == result
        await child.stop()

    _run(exercise)


def test_only_readable_text_from_block_tool_results_reaches_panels(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        clients[0].say(
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="text-list",
                        content=[
                            {"type": "text", "text": "first"},
                            {"type": "text", "text": "second"},
                        ],
                    ),
                    ToolResultBlock(
                        tool_use_id="mixed",
                        content=[
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "binary-image",
                                },
                            },
                            {"type": "text", "text": "caption"},
                        ],
                    ),
                    ToolResultBlock(
                        tool_use_id="image-only",
                        content=[
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "binary-image",
                                },
                            }
                        ],
                    ),
                    ToolResultBlock(
                        tool_use_id="other-non-text",
                        content=[{"type": "document", "source": {"type": "file"}}],
                    ),
                ]
            )
        )
        await clients[0].until_taken_in()

        assert [finished["detail"] for finished in sink.tools_finished] == [
            "first\nsecond",
            "caption",
            None,
            None,
        ]
        await child.stop()

    _run(exercise)


def test_a_tool_call_that_went_wrong_is_recorded_as_a_failure(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        clients[0].say(
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tool-1", content="no such file", is_error=True
                    )
                ]
            )
        )
        await clients[0].until_taken_in()

        assert sink.tools_finished[0]["tool_call_status"] is ToolCallStatus.failed
        await child.stop()

    _run(exercise)


def test_a_subagents_own_talk_stays_inside_its_tool_call(tmp_path: Path) -> None:
    """It is the tool call that happened; its inner conversation is nobody's message.

    What it says while it works is shown against the call it is happening inside — live,
    like any other half-finished text, and kept no more than that.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _assistant(TextBlock(text="inner"), session_id=session_id, parent="tool-1"),
            StreamEvent(
                uuid="u",
                session_id=session_id,
                parent_tool_use_id="tool-1",
                event={
                    "type": "content_block_delta",
                    "delta": {"type": "text_delta", "text": "inner"},
                },
            ),
        )
        await clients[0].until_taken_in()

        assert sink.message_texts == []
        assert sink.deltas == []
        # Shown against the call it came from, under the id that call started under.
        assert sink.tools_progressed == [("tool-1", "inner")]
        await child.stop()

    _run(exercise)


def test_a_subagents_own_tool_results_are_not_this_conversations_finishes(
    tmp_path: Path,
) -> None:
    """A finish for a call that never started would be a record about nothing.

    The tool calls a subagent makes belong to its inner conversation and are never
    reported as started here, so their results are not reported as finished either.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="inner-1", content="done", is_error=False
                    )
                ],
                parent_tool_use_id="tool-1",
            ),
        )
        await clients[0].until_taken_in()

        assert sink.tools_finished == []
        await child.stop()

    _run(exercise)


def test_the_todo_list_claude_keeps_for_itself_is_this_conversations_plan(
    tmp_path: Path,
) -> None:
    """Claude has no plan on its wire — it has a tool it keeps a todo list in.

    The list is the tool's input, so the plan is read from the call as it is made, which
    is also the moment it changes. The call is still recorded as a tool call either way.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _assistant(
                ToolUseBlock(
                    id="tool-1",
                    name="TodoWrite",
                    input={
                        "todos": [
                            {"content": "read the code", "status": "completed"},
                            {"content": "write the thing", "status": "in_progress"},
                            {"content": "run the tests", "status": "pending"},
                        ]
                    },
                ),
                session_id=session_id,
            ),
        )
        await clients[0].until_taken_in()

        assert sink.plans == [
            [
                ("read the code", "completed"),
                ("write the thing", "in_progress"),
                ("run the tests", "pending"),
            ]
        ]
        assert [started["tool_call_id"] for started in sink.tools_started] == ["tool-1"]
        await child.stop()

    _run(exercise)


def test_a_todo_list_that_is_not_the_shape_it_should_be_is_no_plan_at_all(
    tmp_path: Path,
) -> None:
    """A wrong plan on the screen is worse than none, so doubt reads as nothing.

    The tool call itself is still recorded: what could not be understood is the plan, not
    the fact that claude called the tool.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _assistant(
                ToolUseBlock(
                    id="tool-1",
                    name="TodoWrite",
                    input={"todos": [{"content": "do it", "status": "half-done"}]},
                ),
                session_id=session_id,
            ),
            _assistant(
                ToolUseBlock(
                    id="tool-2", name="TodoWrite", input={"todos": "not a list"}
                ),
                session_id=session_id,
            ),
            _assistant(
                ToolUseBlock(id="tool-3", name="Bash", input={"command": "ls"}),
                session_id=session_id,
            ),
        )
        await clients[0].until_taken_in()

        assert sink.plans == []
        assert [started["tool_call_id"] for started in sink.tools_started] == [
            "tool-1",
            "tool-2",
            "tool-3",
        ]
        await child.stop()

    _run(exercise)


def test_news_this_adapter_has_no_use_for_never_stops_the_stream(
    tmp_path: Path,
) -> None:
    """An SDK that grows a message type is not an adapter that falls over."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            SystemMessage(subtype="something_new", data={"session_id": session_id}),
            _assistant(TextBlock(text="still here"), session_id=session_id),
        )
        await clients[0].until_taken_in()

        assert sink.message_texts == [(TURN, "still here")]
        await child.stop()

    _run(exercise)


# --- how a turn ends ---------------------------------------------------------------------------


def test_an_ordinary_parent_message_after_a_result_is_still_reported(
    tmp_path: Path,
) -> None:
    """Claude may continue its persistent run after saying that one turn completed."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _assistant(
                TextBlock(text="the first parent message"), session_id=session_id
            ),
            _result(session_id=session_id),
            _assistant(
                TextBlock(text="the delayed parent message"), session_id=session_id
            ),
            _result(session_id=session_id),
        )
        await clients[0].until_taken_in()

        assert sink.message_texts == [
            (TURN, "the first parent message"),
            (TURN, "the delayed parent message"),
        ]
        assert len(sink.endings) == 1
        await child.stop()

    _run(exercise)


def test_parent_messages_are_not_lost_when_a_successor_prompt_interleaves(
    tmp_path: Path,
) -> None:
    """Claude gives no turn id here; the durable conversation still keeps every message."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child, "first")
        clients[0].say(_result(session_id=session_id))
        await clients[0].until_taken_in()

        await _write(child, "second", TURN_2)
        clients[0].say(
            _assistant(TextBlock(text="late first reply"), session_id=session_id),
            _result(session_id=session_id),
            _assistant(TextBlock(text="second reply"), session_id=session_id),
            _result(session_id=session_id),
        )
        await clients[0].until_taken_in()

        assert [text for _, text in sink.message_texts] == [
            "late first reply",
            "second reply",
        ]
        assert [ending["turn"] for ending in sink.endings] == [TURN, TURN_2]
        await child.stop()

    _run(exercise)


def test_a_turn_that_finished_is_recorded_as_completed(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(_result(session_id=session_id))
        await clients[0].until_taken_in()

        assert sink.endings == [
            {
                "turn": TURN,
                "ending": ConversationTurnEnding.completed,
                "error_summary": None,
                "standard_error_tail": None,
            }
        ]
        await child.stop()

    _run(exercise)


def test_a_turn_that_failed_carries_what_went_wrong(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        stderr = clients[0].options.stderr
        assert stderr is not None
        stderr("something went wrong inside claude")
        await _write(child)
        clients[0].say(
            _result(
                session_id=session_id,
                subtype="error_during_execution",
                is_error=True,
                errors=["the api said no"],
            )
        )
        await clients[0].until_taken_in()

        assert sink.endings[0]["ending"] is ConversationTurnEnding.failed
        assert sink.endings[0]["error_summary"] == "the api said no"
        assert (
            sink.endings[0]["standard_error_tail"]
            == "something went wrong inside claude"
        )
        await child.stop()

    _run(exercise)


def test_a_turn_the_cli_says_was_aborted_is_an_interruption(tmp_path: Path) -> None:
    """The CLI's own name for a stopped turn, not a reading of an error's wording."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _result(session_id=session_id, terminal_reason="aborted_streaming")
        )
        await clients[0].until_taken_in()

        assert sink.endings[0]["ending"] is ConversationTurnEnding.interrupted
        await child.stop()

    _run(exercise)


def test_a_turn_this_adapter_stopped_is_an_interruption_whatever_it_reports(
    tmp_path: Path,
) -> None:
    """Having asked for the interrupt is a fact this child owns, so it needs no signal."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        await _cancel_with_result(
            child,
            clients[0],
            _result(
                session_id=session_id,
                subtype="error_during_execution",
                is_error=True,
                terminal_reason=None,
            ),
        )
        assert clients[0].interrupts == 1

        assert sink.endings[0]["ending"] is ConversationTurnEnding.interrupted
        assert sink.endings[0]["error_summary"] is None
        # The child is still up: the next turn continues the same conversation.
        await _write(child, "carry on")
        assert clients[0].prompts == ["owner:\nhello", "owner:\ncarry on"]
        await child.stop()

    _run(exercise)


def test_a_child_whose_stream_ends_mid_turn_fails_the_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        clients[0].end_the_stream()
        await clients[0].until_taken_in()

        assert sink.endings[0]["ending"] is ConversationTurnEnding.failed
        with pytest.raises(NeedsRebind):
            await _write(child, "follow-up")
        assert clients[0].prompts == ["owner:\nhello"]
        assert sink.message_texts == []
        await child.stop()

    _run(exercise)


# --- what has been spent -------------------------------------------------------------------------

# A result message's counts as claude 2.1.220 sends them. They are the session's running
# totals: the CLI adds every request into one tally and puts that tally on every result.
CLAUDE_COUNTS: dict[str, Any] = {
    "input_tokens": 120,
    "output_tokens": 45,
    "cache_read_input_tokens": 9000,
    "cache_creation_input_tokens": 300,
    "server_tool_use": {"web_search_requests": 0},
}


def test_the_counts_claude_gave_are_reported_before_the_turn_is_closed(
    tmp_path: Path,
) -> None:
    """Claude is the one backend with real money in it, and this is where it says how much.

    The money is ``total_cost_usd``, which is claude's own name for the cost. It goes before
    the ending because the ending stops this turn being the running one, and what is said
    about a turn that is over is dropped.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _result(
                session_id=session_id, usage=dict(CLAUDE_COUNTS), total_cost_usd=0.0731
            )
        )
        await clients[0].until_taken_in()

        assert sink.token_usage == [
            {
                "turn": TURN,
                "input_tokens": 120,
                "output_tokens": 45,
                "cached_input_tokens": 9000,
                "cost_usd": 0.0731,
            }
        ]
        assert sink.calls_in_order == ["token_usage", "turn_ended"]
        await child.stop()

    _run(exercise)


@pytest.mark.parametrize(
    ("usage", "cached_input_tokens", "input_tokens"),
    [
        pytest.param(
            {"input_tokens": 120, "output_tokens": 45},
            None,
            120,
            id="silent about cached tokens",
        ),
        pytest.param(None, None, None, id="silent about every count"),
    ],
)
def test_a_count_claude_did_not_give_is_nothing_rather_than_zero(
    tmp_path: Path,
    usage: dict[str, Any] | None,
    cached_input_tokens: int | None,
    input_tokens: int | None,
) -> None:
    """A result message that says nothing about cached tokens has not said there were none.

    Zero is a count claude gave. Absent is claude not counting, and the two are read very
    differently by anyone looking at what a conversation cost.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(_result(session_id=session_id, usage=usage, total_cost_usd=None))
        await clients[0].until_taken_in()

        assert sink.token_usage == [
            {
                "turn": TURN,
                "input_tokens": input_tokens,
                "output_tokens": 45 if usage is not None else None,
                "cached_input_tokens": cached_input_tokens,
                "cost_usd": None,
            }
        ]
        await child.stop()

    _run(exercise)


def test_a_turn_that_was_stopped_still_says_what_has_been_spent(tmp_path: Path) -> None:
    """An interrupted turn spent the money it spent before somebody stopped it."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        await _cancel_with_result(
            child,
            clients[0],
            _result(
                session_id=session_id,
                terminal_reason="aborted_streaming",
                usage=dict(CLAUDE_COUNTS),
                total_cost_usd=0.0731,
            ),
        )

        assert sink.endings[0]["ending"] is ConversationTurnEnding.interrupted
        assert [report["cost_usd"] for report in sink.token_usage] == [0.0731]
        assert sink.calls_in_order == ["token_usage", "turn_ended"]
        await child.stop()

    _run(exercise)


# --- when claude drops what it has summarised --------------------------------------------------


def test_a_compaction_is_reported_and_the_rest_of_the_news_is_left_alone(
    tmp_path: Path,
) -> None:
    """Claude summarises what came before and drops it, and says so with a system message.

    Nothing about what was summarised travels: that it happened, and where in the thread, is
    the whole of what a reader needs. Every other system message stays what it was — news
    this adapter has no use for, which does not stop the stream.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            SystemMessage(
                subtype="compact_boundary",
                data={
                    "session_id": session_id,
                    "compactMetadata": {"trigger": "auto", "pre_tokens": 150000},
                },
            ),
            SystemMessage(subtype="something_new", data={"session_id": session_id}),
            _assistant(TextBlock(text="carrying on"), session_id=session_id),
        )
        await clients[0].until_taken_in()

        assert sink.compactions == [TURN]
        assert sink.calls_in_order == ["context_compacted"]
        assert sink.message_texts == [(TURN, "carrying on")]
        await child.stop()

    _run(exercise)


# --- permission asks ----------------------------------------------------------------------------


async def _raise_an_ask(
    client: _ScriptedClaudeSdkClient,
    sink: _RecordingSink,
    *,
    suggestions: list[PermissionUpdate] | None = None,
) -> asyncio.Task[PermissionResult]:
    """Have the SDK ask for permission, and wait until the core has been told about it.

    The ask goes in through the callback the adapter put on the options, which is the way
    the SDK itself raises one.
    """
    callback = client.options.can_use_tool
    assert callback is not None

    async def ask() -> PermissionResult:
        return await callback(
            "Bash",
            {"command": "rm -rf /"},
            ToolPermissionContext(tool_use_id="tool-1", suggestions=suggestions or []),
        )

    asking = asyncio.create_task(ask())
    while not sink.asks:
        await asyncio.sleep(0)
    return asking


def test_an_ask_waits_and_the_answer_is_the_one_that_was_offered(
    tmp_path: Path,
) -> None:
    """The options are this adapter's three, and an allow goes back with the call's input."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_an_ask(clients[0], sink)

        ask = sink.asks[0]
        assert ask.title == "Bash"
        assert ask.detail == '{"command": "rm -rf /"}'
        assert [option.option_id for option in ask.options] == [
            APPROVE_ONCE_OPTION_ID,
            ALWAYS_ALLOW_THIS_SESSION_OPTION_ID,
            DECLINE_OPTION_ID,
        ]
        assert not asking.done()

        await child.answer_permission_ask(ask.ask_id, APPROVE_ONCE_OPTION_ID)
        answer = await asking
        assert isinstance(answer, PermissionResultAllow)
        assert answer.updated_input == {"command": "rm -rf /"}
        assert answer.updated_permissions is None
        await child.stop()

    _run(exercise)


def test_always_allow_carries_the_scope_the_sdk_suggested(tmp_path: Path) -> None:
    """Only the SDK can say what "and not again this session" means, so only it is sent."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        suggested = PermissionUpdate(type="addRules")
        asking = await _raise_an_ask(clients[0], sink, suggestions=[suggested])

        await child.answer_permission_ask(
            sink.asks[0].ask_id, ALWAYS_ALLOW_THIS_SESSION_OPTION_ID
        )
        answer = await asking
        assert isinstance(answer, PermissionResultAllow)
        assert answer.updated_permissions == [suggested]
        await child.stop()

    _run(exercise)


def test_always_allow_with_nothing_suggested_is_an_allow_all_the_same(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_an_ask(clients[0], sink)

        await child.answer_permission_ask(
            sink.asks[0].ask_id, ALWAYS_ALLOW_THIS_SESSION_OPTION_ID
        )
        answer = await asking
        assert isinstance(answer, PermissionResultAllow)
        assert answer.updated_permissions is None
        await child.stop()

    _run(exercise)


def test_a_declined_ask_is_a_denial_the_agent_can_read(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_an_ask(clients[0], sink)

        await child.answer_permission_ask(sink.asks[0].ask_id, DECLINE_OPTION_ID)
        answer = await asking
        assert isinstance(answer, PermissionResultDeny)
        assert answer.message == "User declined tool execution."
        await child.stop()

    _run(exercise)


def test_an_answer_that_was_never_offered_does_not_land(tmp_path: Path) -> None:
    """And the ask is still waiting afterwards, rather than lost to a bad answer."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_an_ask(clients[0], sink)

        with pytest.raises(PermissionAnswerWriteFailed):
            await child.answer_permission_ask(sink.asks[0].ask_id, "shred_it")
        assert not asking.done()

        await child.answer_permission_ask(sink.asks[0].ask_id, DECLINE_OPTION_ID)
        await asking
        await child.stop()

    _run(exercise)


def test_an_answer_to_an_unknown_ask_does_not_land(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, _, _ = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        with pytest.raises(PermissionAnswerWriteFailed):
            await child.answer_permission_ask("no-such-ask", APPROVE_ONCE_OPTION_ID)
        await child.stop()

    _run(exercise)


def test_an_ask_dies_with_its_turn_and_the_sdk_is_told(tmp_path: Path) -> None:
    """No vendor call is left hanging, and the settled ask carries nobody's answer."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        asking = await _raise_an_ask(clients[0], sink)

        clients[0].say(_result(session_id=session_id))
        await clients[0].until_taken_in()

        answer = await asking
        assert isinstance(answer, PermissionResultDeny)
        assert answer.message != "User declined tool execution."
        # The core is told nothing about it: an ask that died carries no answer.
        with pytest.raises(PermissionAnswerWriteFailed):
            await child.answer_permission_ask(
                sink.asks[0].ask_id, APPROVE_ONCE_OPTION_ID
            )
        await child.stop()

    _run(exercise)


def test_stopping_the_child_settles_its_asks_and_closes_the_client(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_an_ask(clients[0], sink)

        await child.stop()
        assert isinstance(await asking, PermissionResultDeny)
        assert clients[0].disconnected is True

    _run(exercise)


# --- when claude is asking rather than asking permission ------------------------------------------

COLOUR_QUESTION = "Which colour do you prefer?"


def _ask_user_question_input(
    *,
    questions: list[dict[str, Any]] | None = None,
    multi_select: bool = False,
    header: str = "Colour",
) -> dict[str, Any]:
    """A call shaped the way claude 2.1.220 shapes one, as captured from the real CLI."""
    if questions is not None:
        return {"questions": questions}
    return {
        "questions": [
            {
                "question": COLOUR_QUESTION,
                "header": header,
                "multiSelect": multi_select,
                "options": [
                    {"label": "Red", "description": "A warm, vibrant colour"},
                    {"label": "Blue", "description": "A cool, calming colour"},
                    {"label": "Green", "description": "A natural, refreshing colour"},
                ],
            }
        ]
    }


async def _raise_a_question(
    client: _ScriptedClaudeSdkClient,
    sink: _RecordingSink,
    *,
    tool_input: dict[str, Any] | None = None,
) -> asyncio.Task[PermissionResult]:
    """Have claude ask the owner something, and wait until the core has been told."""
    callback = client.options.can_use_tool
    assert callback is not None
    asked = tool_input if tool_input is not None else _ask_user_question_input()

    async def ask() -> PermissionResult:
        return await callback(
            "AskUserQuestion", asked, ToolPermissionContext(tool_use_id="tool-q")
        )

    asking = asyncio.create_task(ask())
    while (
        not sink.user_input_requests
        and not sink.user_input_failures
        and not asking.done()
    ):
        await asyncio.sleep(0)
    return asking


def _three_question_input() -> dict[str, Any]:
    return {
        "questions": [
            *_ask_user_question_input()["questions"],
            {
                "question": "Which work should happen?",
                "header": "Scope",
                "multiSelect": True,
                "options": [
                    {"label": "Backend", "description": "Change the Python service"},
                    {"label": "Frontend", "description": "Change the Svelte app"},
                ],
            },
            {
                "question": "When should it ship?",
                "header": "Timing",
                "multiSelect": False,
                "options": [
                    {"label": "Now", "description": "Land it immediately"},
                    {"label": "Later", "description": "Schedule it"},
                ],
            },
        ]
    }


def test_three_questions_are_one_distinct_ordered_user_input_request(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_a_question(
            clients[0], sink, tool_input=_three_question_input()
        )

        assert sink.asks == []
        request = sink.user_input_requests[0]
        assert [question.question_id for question in request.questions] == [
            COLOUR_QUESTION,
            "Which work should happen?",
            "When should it ship?",
        ]
        assert request.questions[1].multi_select is True
        assert all(question.allow_other for question in request.questions)
        assert request.questions[0].options[1].description == "A cool, calming colour"

        await child.answer_user_input(
            request.request_id,
            (
                UserInputAnswer(COLOUR_QUESTION, ("Blue",)),
                UserInputAnswer("Which work should happen?", ("Backend", "Frontend")),
                UserInputAnswer("When should it ship?", ("tomorrow morning",)),
            ),
        )
        answer = await asking
        assert isinstance(answer, PermissionResultAllow)
        assert answer.updated_input is not None
        assert answer.updated_input["answers"] == {
            COLOUR_QUESTION: "Blue",
            "Which work should happen?": "Backend, Frontend",
            "When should it ship?": "tomorrow morning",
        }
        assert answer.updated_input["questions"] == _three_question_input()["questions"]
        await child.stop()

    _run(exercise)


def test_an_incomplete_question_answer_map_does_not_land(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_a_question(clients[0], sink)
        request = sink.user_input_requests[0]

        with pytest.raises(UserInputAnswerWriteFailed):
            await child.answer_user_input(request.request_id, ())
        assert not asking.done()
        await child.answer_user_input(
            request.request_id, (UserInputAnswer(COLOUR_QUESTION, ("Blue",)),)
        )
        await asking
        await child.stop()

    _run(exercise)


def test_a_question_that_dies_with_its_turn_is_settled_as_denied(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        asking = await _raise_a_question(clients[0], sink)
        clients[0].say(_result(session_id=session_id))
        await clients[0].until_taken_in()
        assert isinstance(await asking, PermissionResultDeny)
        await child.stop()

    _run(exercise)


def test_cancelling_settles_the_question_and_waits_for_the_terminal_result(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_a_question(clients[0], sink)

        await _cancel_with_result(
            child, clients[0], _result(terminal_reason="aborted_streaming")
        )

        assert isinstance(await asking, PermissionResultDeny)
        assert clients[0].interrupts == 1
        await child.stop()

    _run(exercise)


@pytest.mark.parametrize(
    "tool_input",
    [
        pytest.param({"questions": []}, id="no-question"),
        pytest.param({"questions": "not a list"}, id="not-a-list"),
        pytest.param(
            {
                "questions": [
                    {
                        "question": "Which?",
                        "header": "H",
                        "multiSelect": False,
                        "options": [],
                    }
                ]
            },
            id="no-options",
        ),
    ],
)
def test_malformed_questions_fail_visibly_and_never_become_permissions(
    tmp_path: Path, tool_input: dict[str, Any]
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        await _write(child)
        asking = await _raise_a_question(clients[0], sink, tool_input=tool_input)
        answer = await asking
        assert isinstance(answer, PermissionResultDeny)
        assert sink.asks == []
        assert "malformed question request" in sink.user_input_failures[0][1]
        await child.stop()

    _run(exercise)


def test_ask_user_question_tool_lifecycle_is_not_rendered_beside_the_question(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(
            _start_request(workspace_folder=tmp_path), vendor_session_cursor=None
        )
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            _assistant(
                ToolUseBlock(
                    id="tool-q",
                    name="AskUserQuestion",
                    input=_three_question_input(),
                ),
                session_id=session_id,
            ),
            UserMessage(
                content=[
                    ToolResultBlock(
                        tool_use_id="tool-q", content="answers received", is_error=False
                    )
                ]
            ),
        )
        await clients[0].until_taken_in()
        assert sink.tools_started == []
        assert sink.tools_finished == []
        await child.stop()

    _run(exercise)


# --- the claude on this machine ------------------------------------------------------------------


@real_claude_only
def test_real_claude_holds_a_conversation_across_a_stop_and_a_resume(
    tmp_path: Path,
) -> None:
    """A codeword given before the child was stopped comes back after it is resumed.

    This is the whole of the durable-session claim: the cursor the adapter minted names a
    session the next child picks the conversation up from.
    """

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        assert len(sink.cursors) == 1
        cursor = sink.cursors[0]

        await _write(child, "Remember the codeword ZARDOZ. Reply with just: OK")
        await _until_the_turn_ends(sink)
        assert sink.endings[-1]["ending"] is ConversationTurnEnding.completed
        await child.stop()

        resumed, resumed_sink, _ = _bench_on_real_claude(resolved_start)
        await resumed.start(resolved_start, vendor_session_cursor=cursor)
        await _write(resumed, "What was the codeword? Reply with just the word.")
        await _until_the_turn_ends(resumed_sink)
        said = " ".join(text for _, text in resumed_sink.message_texts)
        assert "ZARDOZ" in said.upper()
        await resumed.stop()

    _run(exercise, seconds=300.0)


@real_claude_only
def test_real_claude_refuses_a_session_it_does_not_have(tmp_path: Path) -> None:
    """The named build obligation, against the CLI itself: never a fresh thread in silence."""

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, _, _ = _bench_on_real_claude(resolved_start)
        with pytest.raises(SessionLoadFailed) as would_not_load:
            await child.start(
                resolved_start,
                vendor_session_cursor="00000000-0000-4000-8000-000000000000",
            )
        assert "No conversation found" in str(would_not_load.value)

    _run(exercise, seconds=120.0)


@real_claude_only
def test_real_claude_accepts_a_uuid_steer_and_keeps_one_panels_turn(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        await _write(
            child,
            "Run one foreground Bash call: `sleep 4; echo first`. "
            "After it returns, reply with exactly ORIGINAL-DONE.",
        )
        while not sink.tools_started:
            await asyncio.sleep(0.1)

        outcome = await child.steer(
            TURN,
            text_message_content(
                "After the current command, include PANELS-FIRST-STEER in your reply."
            ),
            sender_label="owner",
        )
        assert isinstance(outcome, BackendSteerAccepted)
        second_outcome = await child.steer(
            TURN,
            text_message_content(
                "Also include PANELS-SECOND-STEER in your final reply."
            ),
            sender_label="owner",
        )
        assert isinstance(second_outcome, BackendSteerAccepted)
        await _until_the_turn_ends(sink)
        assert len(sink.endings) == 1
        assert sink.endings[0]["turn"] == TURN
        assert sink.token_usage
        assert all(report["turn"] == TURN for report in sink.token_usage)
        assert all(token == TURN for token, _ in sink.message_texts)
        assert sink.tools_finished
        assert all(tool["turn"] == TURN for tool in sink.tools_started)
        assert all(tool["turn"] == TURN for tool in sink.tools_finished)
        assert sink.calls_in_order[-1] == "turn_ended"
        said = " ".join(text for _, text in sink.message_texts)
        assert "PANELS-FIRST-STEER" in said
        assert "PANELS-SECOND-STEER" in said
        await child.stop()

    _run(exercise, seconds=300.0)


@real_claude_only
def test_real_claude_stops_a_running_turn_when_it_is_cancelled(tmp_path: Path) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        await _write(
            child,
            "Run one foreground Bash command: `sleep 60; echo finished`. "
            "After it returns, reply with exactly FINISHED.",
        )
        while not sink.tools_started:
            await asyncio.sleep(0.1)
        steer_outcome = await child.steer(
            TURN,
            text_message_content(
                "After the command, reply with exactly MUST-NOT-RUN-AFTER-STOP."
            ),
            sender_label="owner",
        )
        assert isinstance(steer_outcome, BackendSteerAccepted)
        await child.cancel_running_turn()
        assert sink.endings[-1]["ending"] is ConversationTurnEnding.interrupted

        await _write(child, "Reply with exactly RESUMED-AFTER-STOP.", TURN_2)
        await _until_the_turn_ends(sink)
        resumed_ending = sink.endings[-1]
        assert resumed_ending["turn"] == TURN_2
        assert resumed_ending["ending"] is ConversationTurnEnding.completed
        assert "RESUMED-AFTER-STOP" in " ".join(
            text for token, text in sink.message_texts if token == TURN_2
        )
        assert "MUST-NOT-RUN-AFTER-STOP" not in " ".join(
            text for _, text in sink.message_texts
        )
        await child.stop()

    _run(exercise, seconds=300.0)


@real_claude_only
def test_real_claude_keeps_the_conversation_across_a_model_change(
    tmp_path: Path,
) -> None:
    """The rebind is the core's, and this is the half the adapter owes it: the same session
    comes back up on the new model with the conversation intact."""

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        cursor = sink.cursors[0]
        await _write(child, "Remember the codeword XANADU. Reply with just: OK")
        await _until_the_turn_ends(sink)

        with pytest.raises(NeedsRebind):
            content = text_message_content(
                "What was the codeword? Reply with just the word."
            )
            await child.write_prompt(
                TURN,
                content,
                sender_content=content,
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=CLAUDE_OTHER_MODEL,
                reasoning_effort_change=None,
            )
        await child.stop()

        on_the_new_model = _start_request(
            workspace_folder=tmp_path, model=CLAUDE_OTHER_MODEL
        )
        rebound, rebound_sink, _ = _bench_on_real_claude(on_the_new_model)
        await rebound.start(on_the_new_model, vendor_session_cursor=cursor)
        await rebound.write_prompt(
            TURN,
            content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            model_change=CLAUDE_OTHER_MODEL,
            reasoning_effort_change=None,
        )
        await _until_the_turn_ends(rebound_sink)
        said = " ".join(text for _, text in rebound_sink.message_texts)
        assert "XANADU" in said.upper()
        await rebound.stop()

    _run(exercise, seconds=420.0)


@real_claude_only
def test_real_claude_is_told_the_answer_the_owner_chose(tmp_path: Path) -> None:
    """The whole of the claim, against the CLI: claude asks, the owner answers, claude knows.

    The proof is claude's own next sentence naming the colour that was chosen here. Anything
    less — the ask rendering nicely, the callback returning — would not show that the answer
    reached the model at all.
    """

    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        await _write(
            child,
            "Use the AskUserQuestion tool to ask me whether I prefer the colour red, blue "
            "or green. After I answer, reply with exactly one sentence naming the colour I "
            "chose.",
        )

        async def until_asked() -> None:
            while not sink.user_input_requests:
                await asyncio.sleep(0.1)

        await asyncio.wait_for(until_asked(), 120.0)
        request = sink.user_input_requests[0]
        question = request.questions[0]
        blue = next(
            option for option in question.options if "blue" in option.label.lower()
        )

        await child.answer_user_input(
            request.request_id,
            (UserInputAnswer(question.question_id, (blue.label,)),),
        )
        await _until_the_turn_ends(sink)
        assert sink.endings[-1]["ending"] is ConversationTurnEnding.completed
        said = " ".join(text for _, text in sink.message_texts).lower()
        assert "blue" in said
        assert "did not answer" not in said
        await child.stop()

    _run(exercise, seconds=300.0)


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


def test_a_message_that_is_only_words_still_goes_as_the_string_it_always_did(
    tmp_path: Path,
) -> None:
    """The labeled common case still uses the SDK's direct string form.

    The SDK wraps a string in exactly the envelope the richer form builds by hand, so
    the richer form is reached only by a message that needs it.
    """

    async def exercise() -> None:
        child, _, clients = await _connected_bench(tmp_path)
        await _write(child, "just words")

        assert clients[0].prompts == ["owner:\njust words"]
        assert clients[0].streamed_messages == []

    _run(exercise)


def test_a_picture_reaches_claude_as_a_content_block_beside_the_words(
    tmp_path: Path,
) -> None:
    """Claude takes a picture as base64 inside a user message, so that is what it is sent."""

    async def exercise() -> None:
        child, _, clients = await _connected_bench(tmp_path)
        kept = await _bench_message_files(child).keep(
            "c-claude-1", b"\x89PNG not really", media_type="image/png"
        )

        content = (
            MessageText(text="look at this"),
            MessageImage(stored_file_id=kept.stored_file_id, media_type="image/png"),
        )
        await child.write_prompt(
            TURN,
            content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            model_change=None,
            reasoning_effort_change=None,
        )

        assert clients[0].prompts == []
        assert len(clients[0].streamed_messages) == 1
        sent = clients[0].streamed_messages[0]
        assert sent["type"] == "user"
        assert sent["message"]["role"] == "user"
        assert sent["message"]["content"] == [
            {"type": "text", "text": "owner:\nlook at this"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": b64encode(b"\x89PNG not really").decode("ascii"),
                },
            },
        ]

    _run(exercise)


def test_a_file_reaches_claude_as_explicit_managed_path_context(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, _, clients = await _connected_bench(tmp_path)
        kept = await _bench_message_files(child).keep(
            "c-claude-1", b"answer,42\n", media_type="text/csv"
        )
        content = (
            MessageFile(
                stored_file_id=kept.stored_file_id,
                media_type="text/csv",
                file_name="facts.csv",
                byte_count=10,
            ),
        )
        await child.write_prompt(
            TURN,
            content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            model_change=None,
            reasoning_effort_change=None,
        )
        sent = clients[0].streamed_messages[0]
        assert sent["message"]["content"] == [
            {"type": "text", "text": "owner:"},
            {
                "type": "text",
                "text": (
                    'Attached file "facts.csv" (text/csv, 10 bytes) is available at '
                    f"{kept.absolute_path.resolve()}."
                ),
            }
        ]

    _run(exercise)
