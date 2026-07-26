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
import os
import shutil
from base64 import b64encode
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

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
    UserMessage,
)

from planner.conversation.backends.claude_agent_sdk import (
    ALWAYS_ALLOW_THIS_SESSION_OPTION_ID,
    APPROVE_ONCE_OPTION_ID,
    DECLINE_OPTION_ID,
    ClaudeAgentSdkBackendChild,
    ClaudeAgentSdkBackendChildFactory,
    ClaudeAgentSdkChildLaunch,
)
from planner.conversation.backends.contracts import (
    BackendChild,
    BackendChildFactory,
    BackendPermissionAsk,
    BackendSpawnFailed,
    NeedsRebind,
    PermissionAnswerWriteFailed,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding, ToolCallStatus
from planner.conversation.message_content import (
    MessageContent,
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

REAL_CLAUDE_TESTS_ENVIRONMENT_NAME = "PANELS_REAL_CLAUDE_TESTS"
CLAUDE_EXECUTABLE = shutil.which("claude")

# The cheapest model to prove a real turn with, and one other to prove a model change.
CLAUDE_MODEL = "haiku"
CLAUDE_OTHER_MODEL = "sonnet"

real_claude_only = pytest.mark.skipif(
    os.environ.get(REAL_CLAUDE_TESTS_ENVIRONMENT_NAME) != "1" or CLAUDE_EXECUTABLE is None,
    reason=f"set {REAL_CLAUDE_TESTS_ENVIRONMENT_NAME}=1 with claude installed to run this",
)


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 30.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


# --- the scripted client and the recording sink --------------------------------------------


class _ScriptedClaudeSdkClient:
    """A claude client that says what a test tells it to, and remembers what it was asked."""

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.prompts: list[str] = []
        # What a message with more than words in it was actually sent as. The SDK takes
        # either a string or a stream of user messages; this keeps the second, drained,
        # so a test can see the blocks rather than an exhausted generator.
        self.streamed_messages: list[dict[str, Any]] = []
        self.interrupts = 0
        self.disconnected = False
        self.connect_failure: BaseException | None = None
        self.query_failure: BaseException | None = None
        self._inbox: asyncio.Queue[Message | None] = asyncio.Queue()

    async def connect(self) -> None:
        if self.connect_failure is not None:
            raise self.connect_failure

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
        self.endings: list[dict[str, Any]] = []
        self.cursors: list[str] = []
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
        return [(token, message_content_text(content)) for token, content in self.message_contents]

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

    async def permission_ask_raised(self, turn_token: TurnToken, ask: BackendPermissionAsk) -> None:
        del turn_token
        self.asks.append(ask)

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
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


def _start_request(
    *,
    workspace_folder: Path,
    model: str | None = None,
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
) -> tuple[ClaudeAgentSdkBackendChild, _RecordingSink, list[_ScriptedClaudeSdkClient]]:
    """A child wired to a scripted client, and the clients it has been given."""
    clients: list[_ScriptedClaudeSdkClient] = []

    def make(options: ClaudeAgentOptions) -> _ScriptedClaudeSdkClient:
        client = _ScriptedClaudeSdkClient(options)
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


async def _write(child: ClaudeAgentSdkBackendChild, text: str = "hello") -> None:
    await child.write_prompt(
        TURN,
        text_message_content(text),
        sender_label="owner",
        mode=PromptDeliveryMode.run_when_free,
        model_change=None,
        reasoning_effort_change=None,
    )


def _result(
    *,
    session_id: str = SESSION_ID,
    subtype: str = "success",
    is_error: bool = False,
    terminal_reason: str | None = "completed",
    errors: list[str] | None = None,
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
    )


def _assistant(
    *blocks: Any, session_id: str = SESSION_ID, parent: str | None = None
) -> AssistantMessage:
    return AssistantMessage(
        content=list(blocks), model="claude", session_id=session_id, parent_tool_use_id=parent
    )


# --- the seam ---------------------------------------------------------------------------------


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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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


def test_a_session_that_will_not_load_is_never_replaced_by_a_fresh_one(tmp_path: Path) -> None:
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
        child, _, _ = _bench_that_will_not_connect(resolved_start, RuntimeError("claude fell over"))
        with pytest.raises(BackendSpawnFailed):
            await child.start(resolved_start, vendor_session_cursor=None)

    _run(exercise)


def test_a_missing_executable_is_a_spawn_failure_even_under_a_cursor(tmp_path: Path) -> None:
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
        ClaudeAgentSdkChildLaunch(claude_executable=Path("/usr/bin/claude")), client_factory=make
    )
    return factory(
        resolved_start=resolved_start, event_sink=sink, message_files=_message_files()
    ), sink, clients


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
        # Nothing more is written to a child that is not this conversation's session.
        with pytest.raises(PromptWriteFailed):
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
            SystemMessage(subtype="hook_progress", data={"session_id": ANOTHER_SESSION_ID}),
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
        resolved_start = _start_request(workspace_folder=tmp_path, reasoning_effort="ludicrous")
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
        resolved_start = _start_request(workspace_folder=tmp_path, model="claude-haiku-4-5")
        child, _, clients = _bench(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        with pytest.raises(NeedsRebind):
            await child.write_prompt(
                TURN,
                text_message_content("on the other model please"),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change="claude-sonnet-4-5",
                reasoning_effort_change=None,
            )
        assert clients[0].prompts == []
        await child.stop()

    _run(exercise)


def test_a_reasoning_effort_change_asks_for_a_child_started_on_it(tmp_path: Path) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, reasoning_effort="low")
        child, _, clients = _bench(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        with pytest.raises(NeedsRebind):
            await child.write_prompt(
                TURN,
                text_message_content("think harder"),
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
            workspace_folder=tmp_path, model="claude-sonnet-4-5", reasoning_effort="high"
        )
        child, _, clients = _bench(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=SESSION_ID)
        await child.write_prompt(
            TURN,
            text_message_content("on the other model please"),
            sender_label="owner",
            mode=PromptDeliveryMode.run_when_free,
            model_change="claude-sonnet-4-5",
            reasoning_effort_change="high",
        )
        assert clients[0].prompts == ["on the other model please"]
        await child.stop()

    _run(exercise)


def test_the_label_and_the_mode_are_taken_and_dropped(tmp_path: Path) -> None:
    """The SDK's wire carries a user message and nothing alongside it, so they go nowhere.

    The turn runs the same either way, which is what the seam says of a backend with no
    channel for a message's own metadata.
    """

    async def exercise() -> None:
        child, _, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await child.write_prompt(
            TURN,
            text_message_content("hello"),
            sender_label="the automatic loop",
            mode=PromptDeliveryMode.send_now,
            model_change=None,
            reasoning_effort_change=None,
        )
        assert clients[0].prompts == ["hello"]
        await child.stop()

    _run(exercise)


def test_claude_cannot_take_text_into_a_running_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, _, _ = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        with pytest.raises(PromptWriteFailed):
            await child.steer(text_message_content("go left"), sender_label="owner")
        await child.stop()

    _run(exercise)


def test_a_prompt_that_does_not_reach_the_wire_says_so(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, _, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        clients[0].query_failure = BrokenPipeError("the child has gone")
        with pytest.raises(PromptWriteFailed):
            await _write(child)
        # A wire that has failed once fails the same way afterwards rather than hanging.
        clients[0].query_failure = None
        with pytest.raises(PromptWriteFailed):
            await _write(child)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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


def test_streamed_text_is_shown_and_the_finished_message_is_the_row(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
                content=[ToolResultBlock(tool_use_id="tool-1", content="a.txt", is_error=False)]
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


def test_a_tool_call_that_went_wrong_is_recorded_as_a_failure(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        clients[0].say(
            UserMessage(
                content=[
                    ToolResultBlock(tool_use_id="tool-1", content="no such file", is_error=True)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(
            UserMessage(
                content=[ToolResultBlock(tool_use_id="inner-1", content="done", is_error=False)],
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
                ToolUseBlock(id="tool-2", name="TodoWrite", input={"todos": "not a list"}),
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


def test_news_this_adapter_has_no_use_for_never_stops_the_stream(tmp_path: Path) -> None:
    """An SDK that grows a message type is not an adapter that falls over."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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


def test_a_turn_that_finished_is_recorded_as_completed(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        assert sink.endings[0]["standard_error_tail"] == "something went wrong inside claude"
        await child.stop()

    _run(exercise)


def test_a_turn_the_cli_says_was_aborted_is_an_interruption(tmp_path: Path) -> None:
    """The CLI's own name for a stopped turn, not a reading of an error's wording."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        clients[0].say(_result(session_id=session_id, terminal_reason="aborted_streaming"))
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        await child.cancel_running_turn()
        assert clients[0].interrupts == 1
        clients[0].say(
            _result(
                session_id=session_id,
                subtype="error_during_execution",
                is_error=True,
                terminal_reason=None,
            )
        )
        await clients[0].until_taken_in()

        assert sink.endings[0]["ending"] is ConversationTurnEnding.interrupted
        assert sink.endings[0]["error_summary"] is None
        # The child is still up: the next turn continues the same conversation.
        await _write(child, "carry on")
        assert clients[0].prompts == ["hello", "carry on"]
        await child.stop()

    _run(exercise)


def test_a_child_whose_stream_ends_mid_turn_fails_the_turn(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        clients[0].end_the_stream()
        await clients[0].until_taken_in()

        assert sink.endings[0]["ending"] is ConversationTurnEnding.failed
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


def test_an_ask_waits_and_the_answer_is_the_one_that_was_offered(tmp_path: Path) -> None:
    """The options are this adapter's three, and an allow goes back with the call's input."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        suggested = PermissionUpdate(type="addRules")
        asking = await _raise_an_ask(clients[0], sink, suggestions=[suggested])

        await child.answer_permission_ask(sink.asks[0].ask_id, ALWAYS_ALLOW_THIS_SESSION_OPTION_ID)
        answer = await asking
        assert isinstance(answer, PermissionResultAllow)
        assert answer.updated_permissions == [suggested]
        await child.stop()

    _run(exercise)


def test_always_allow_with_nothing_suggested_is_an_allow_all_the_same(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        asking = await _raise_an_ask(clients[0], sink)

        await child.answer_permission_ask(sink.asks[0].ask_id, ALWAYS_ALLOW_THIS_SESSION_OPTION_ID)
        answer = await asking
        assert isinstance(answer, PermissionResultAllow)
        assert answer.updated_permissions is None
        await child.stop()

    _run(exercise)


def test_a_declined_ask_is_a_denial_the_agent_can_read(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        with pytest.raises(PermissionAnswerWriteFailed):
            await child.answer_permission_ask("no-such-ask", APPROVE_ONCE_OPTION_ID)
        await child.stop()

    _run(exercise)


def test_an_ask_dies_with_its_turn_and_the_sdk_is_told(tmp_path: Path) -> None:
    """No vendor call is left hanging, and the settled ask carries nobody's answer."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
            await child.answer_permission_ask(sink.asks[0].ask_id, APPROVE_ONCE_OPTION_ID)
        await child.stop()

    _run(exercise)


def test_stopping_the_child_settles_its_asks_and_closes_the_client(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
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
        return await callback("AskUserQuestion", asked, ToolPermissionContext(tool_use_id="tool-q"))

    asking = asyncio.create_task(ask())
    while not sink.asks:
        await asyncio.sleep(0)
    return asking


def test_a_question_is_raised_as_the_question_and_its_own_choices(tmp_path: Path) -> None:
    """The owner is shown what they were asked, not a tool call to approve.

    The choices are the question's own, and none of them is an allow or a reject — which is
    what tells a surface to render numbered answers instead of approval buttons.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        asking = await _raise_a_question(clients[0], sink)

        ask = sink.asks[0]
        assert ask.title == f"Colour: {COLOUR_QUESTION}"
        assert [option.label for option in ask.options] == ["Red", "Blue", "Green"]
        # The label is the answer, so it is also what goes back as the option id.
        assert [option.option_id for option in ask.options] == ["Red", "Blue", "Green"]
        assert {option.option_kind for option in ask.options} == {"choice"}
        assert not any(option.option_kind.startswith(("allow", "reject")) for option in ask.options)
        # What each answer means, in plain lines. Never the call's JSON.
        assert ask.detail == (
            "Red — A warm, vibrant colour\n"
            "Blue — A cool, calming colour\n"
            "Green — A natural, refreshing colour"
        )
        assert "questions" not in str(ask.detail)

        await child.answer_permission_ask(ask.ask_id, "Blue")
        await asking
        await child.stop()

    _run(exercise)


def test_a_questions_answer_is_put_where_the_tool_reads_it(tmp_path: Path) -> None:
    """The chosen answer goes back keyed by the whole question text.

    That is the field the tool takes the owner's answers from, established against the real
    CLI: allowing the call without it runs the tool and tells the model nobody answered.
    """

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        asking = await _raise_a_question(clients[0], sink)

        await child.answer_permission_ask(sink.asks[0].ask_id, "Blue")
        answer = await asking
        assert isinstance(answer, PermissionResultAllow)
        assert answer.updated_input is not None
        assert answer.updated_input["answers"] == {COLOUR_QUESTION: "Blue"}
        # The call itself goes back unchanged around the answer.
        assert answer.updated_input["questions"] == _ask_user_question_input()["questions"]
        assert answer.updated_permissions is None
        await child.stop()

    _run(exercise)


def test_an_answer_the_question_did_not_offer_does_not_land(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        asking = await _raise_a_question(clients[0], sink)

        for never_offered in ("Purple", APPROVE_ONCE_OPTION_ID):
            with pytest.raises(PermissionAnswerWriteFailed):
                await child.answer_permission_ask(sink.asks[0].ask_id, never_offered)
            assert not asking.done()

        await child.answer_permission_ask(sink.asks[0].ask_id, "Red")
        await asking
        await child.stop()

    _run(exercise)


def test_a_question_that_dies_with_its_turn_is_still_settled(tmp_path: Path) -> None:
    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        session_id = clients[0].options.session_id
        assert session_id is not None
        await _write(child)
        asking = await _raise_a_question(clients[0], sink)

        clients[0].say(_result(session_id=session_id))
        await clients[0].until_taken_in()
        assert isinstance(await asking, PermissionResultDeny)
        await child.stop()

    _run(exercise)


@pytest.mark.parametrize(
    "tool_input",
    [
        pytest.param(_ask_user_question_input(multi_select=True), id="an answer that is a set"),
        pytest.param(
            {
                "questions": [
                    {
                        "question": "First?",
                        "header": "One",
                        "options": [{"label": "A", "description": ""}],
                    },
                    {
                        "question": "Second?",
                        "header": "Two",
                        "options": [{"label": "B", "description": ""}],
                    },
                ]
            },
            id="more than one question",
        ),
        pytest.param({"questions": []}, id="no question at all"),
        pytest.param(
            {"questions": [{"question": "Which?", "header": "H", "options": []}]},
            id="a question with no choices",
        ),
        pytest.param(
            {
                "questions": [
                    {
                        "question": "Which?",
                        "header": "H",
                        "options": [
                            {"label": "Same", "description": "one"},
                            {"label": "Same", "description": "two"},
                        ],
                    }
                ]
            },
            id="two choices that answer the same",
        ),
        pytest.param({"questions": "not a list"}, id="nothing this recognises"),
    ],
)
def test_a_question_this_cannot_show_whole_stays_a_plain_permission_ask(
    tmp_path: Path, tool_input: dict[str, Any]
) -> None:
    """Half a question is worse than none: the owner would answer one part and the rest
    would go back unanswered, so the fallback is the ask that was always there."""

    async def exercise() -> None:
        child, sink, clients = _bench(_start_request(workspace_folder=tmp_path))
        await child.start(_start_request(workspace_folder=tmp_path), vendor_session_cursor=None)
        await _write(child)
        asking = await _raise_a_question(clients[0], sink, tool_input=tool_input)

        ask = sink.asks[0]
        assert ask.title == "AskUserQuestion"
        assert [option.option_id for option in ask.options] == [
            APPROVE_ONCE_OPTION_ID,
            ALWAYS_ALLOW_THIS_SESSION_OPTION_ID,
            DECLINE_OPTION_ID,
        ]

        await child.answer_permission_ask(ask.ask_id, DECLINE_OPTION_ID)
        answer = await asking
        assert isinstance(answer, PermissionResultDeny)
        assert answer.message == "User declined tool execution."
        await child.stop()

    _run(exercise)


# --- the claude on this machine ------------------------------------------------------------------


@real_claude_only
def test_real_claude_holds_a_conversation_across_a_stop_and_a_resume(tmp_path: Path) -> None:
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
                resolved_start, vendor_session_cursor="00000000-0000-4000-8000-000000000000"
            )
        assert "No conversation found" in str(would_not_load.value)

    _run(exercise, seconds=120.0)


@real_claude_only
def test_real_claude_stops_a_running_turn_when_it_is_cancelled(tmp_path: Path) -> None:
    async def exercise() -> None:
        resolved_start = _start_request(workspace_folder=tmp_path, model=CLAUDE_MODEL)
        child, sink, _ = _bench_on_real_claude(resolved_start)
        await child.start(resolved_start, vendor_session_cursor=None)
        await _write(child, "Count slowly from 1 to 500, one number per line.")
        while not sink.deltas:
            await asyncio.sleep(0.1)
        await child.cancel_running_turn()
        await _until_the_turn_ends(sink)
        assert sink.endings[-1]["ending"] is ConversationTurnEnding.interrupted
        await child.stop()

    _run(exercise, seconds=300.0)


@real_claude_only
def test_real_claude_keeps_the_conversation_across_a_model_change(tmp_path: Path) -> None:
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
            await child.write_prompt(
                TURN,
                text_message_content("What was the codeword? Reply with just the word."),
                sender_label="owner",
                mode=PromptDeliveryMode.run_when_free,
                model_change=CLAUDE_OTHER_MODEL,
                reasoning_effort_change=None,
            )
        await child.stop()

        on_the_new_model = _start_request(workspace_folder=tmp_path, model=CLAUDE_OTHER_MODEL)
        rebound, rebound_sink, _ = _bench_on_real_claude(on_the_new_model)
        await rebound.start(on_the_new_model, vendor_session_cursor=cursor)
        await rebound.write_prompt(
            TURN,
            text_message_content("What was the codeword? Reply with just the word."),
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
            while not sink.asks:
                await asyncio.sleep(0.1)

        await asyncio.wait_for(until_asked(), 120.0)
        ask = sink.asks[0]
        # Claude asked a question, so the ask carries the question's own choices.
        assert not any(option.option_kind.startswith(("allow", "reject")) for option in ask.options)
        blue = next(option for option in ask.options if "blue" in option.label.lower())

        await child.answer_permission_ask(ask.ask_id, blue.option_id)
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
    return factory(
        resolved_start=resolved_start, event_sink=sink, message_files=_message_files()
    ), sink, None


async def _until_the_turn_ends(sink: _RecordingSink, *, seconds: float = 180.0) -> None:
    ended = len(sink.endings) + 1

    async def wait() -> None:
        while len(sink.endings) < ended:
            await asyncio.sleep(0.1)

    await asyncio.wait_for(wait(), seconds)


def test_a_message_that_is_only_words_still_goes_as_the_string_it_always_did(
    tmp_path: Path,
) -> None:
    """The common case is untouched.

    The SDK wraps a string in exactly the envelope the richer form builds by hand, so
    keeping the string means an ordinary prompt is byte for byte what it has always been
    and the other form is reached only by a message that needs it.
    """

    async def exercise() -> None:
        child, _, clients = await _connected_bench(tmp_path)
        await _write(child, "just words")

        assert clients[0].prompts == ["just words"]
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

        await child.write_prompt(
            TURN,
            (
                MessageText(text="look at this"),
                MessageImage(stored_file_id=kept.stored_file_id, media_type="image/png"),
            ),
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
            {"type": "text", "text": "look at this"},
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

