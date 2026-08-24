"""Claude behind the backend seam: one CLI child, one session, one message stream.

This is the whole of what the conversation system knows about talking to claude. It owns a
``ClaudeSDKClient`` — which owns a Claude Code CLI subprocess — and the session that
conversation resumes from. It owns none of the conversation's rules: it never decides that
a message waits, never decides that an ask has expired, and never writes a row.

Five things about the Claude Agent SDK shape this adapter, and each one is why a piece of
it looks the way it does.

**The client is connected once and prompted many times.** The SDK's streaming-input mode
keeps one CLI child and one session alive across turns, so a follow-up prompt continues the
conversation with no respawn. ``client.query`` writes the user message and returns; the
turn's news arrives on the message stream a background reader consumes, and the turn's
ending is the ``result`` message. A persistent run can then deliver another ordinary
parent message after that result when delegated work returns. That finished message is
still conversation content and is kept under the turn that just ended; late tool activity
and live decoration remain turn-bound. That is why the write, ending, and durable message
are handled separately here.

**Claude mints its session id, unless we mint it first.** A fresh session is started under
a uuid of our own, passed as ``session_id``, so the cursor this conversation resumes from
is valid before the first message arrives rather than after. A resume passes ``resume``
instead — the two cannot be given together — and the id then stays the conversation's own.

**A resume that names a session claude does not have is refused by claude itself.** The CLI
exits during startup with ``No conversation found with session ID: …`` and the SDK's
connect raises, so the adapter turns that into ``SessionLoadFailed`` and no fresh thread is
ever handed back. Because that is the CLI's behavior rather than a promise, the child also
watches the session id on the stream: until a resumed session has answered under the id it
was asked for, an id that differs is a different thread standing in for this conversation's
own, and it is refused rather than adopted. Once the resume has been confirmed, a later
change of id is claude moving the session and is reported as a new cursor.

**Model and reasoning effort are what the child was started on.** They are start-time
options of the CLI process, so a message that carries a change cannot be served by the
child as it stands: the adapter says ``NeedsRebind`` and the core starts a new child on the
new values, from the same session cursor, under the same conversation. A change is
therefore in force before the prompt that carried it is written, and a prompt that never
reaches the wire leaves a child the core discards.

**A terminal stream failure leaves a resumable session behind a broken wire.** The turn
that was running fails. A later prompt meets the broken wire before it writes any bytes,
so the adapter says ``NeedsRebind`` and the core resumes one replacement child. A failure
during the current query remains ``PromptWriteFailed`` because delivery is then uncertain
and the prompt must not be retried.

**Permission asks are a callback the SDK waits on.** ``can_use_tool`` is called with the
tool and its input and does not return until a person has answered or the turn it belongs
to has died, which is exactly how an ask is meant to wait.

The environment the child runs in is this process's own, plus the conversation's identity
variables. ``HOME`` is never among the adapter's own: overriding it moves the macOS login
keychain the CLI reads its stored credentials from, and the CLI then reports itself logged
out. A separate claude account is pointed at with ``CLAUDE_CONFIG_DIR``, which is an
identity variable like any other and needs nothing here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import warnings
from base64 import b64encode
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol, cast

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLINotFoundError,
    EffortLevel,
    HookEventMessage,
    Message,
    PermissionMode,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    RateLimitEvent,
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
from claude_agent_sdk.types import SystemPromptPreset

from planner.conversation.backends.contracts import (
    BackendEventSink,
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
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationAccess,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    ConversationTurnEnding,
    PermissionAskOption,
    PlanEntry,
    PlanEntryStatus,
    ToolCallStatus,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
)
from planner.conversation.message_content import (
    MessageContent,
    MessageFile,
    MessageImage,
    MessageText,
    text_message_content,
)
from planner.conversation.message_files import (
    ConversationMessageFiles,
    MessageFileMissing,
)

LOGGER = logging.getLogger("planner.conversation.backends.claude_agent_sdk")

# The Claude Code system prompt, which is what makes the child a coding agent rather than a
# bare model. An unset system prompt is not "claude's default" — the SDK sends an empty one.
CLAUDE_CODE_SYSTEM_PROMPT: Final[SystemPromptPreset] = {
    "type": "preset",
    "preset": "claude_code",
}

# The one tool claude keeps a plan in. Its input is the whole list, every time it is
# called, which is why reading the call is reading the plan.
TODO_WRITE_TOOL_NAME: Final = "TodoWrite"

# The reasoning efforts claude has. A conversation asking for anything else is asking for a
# session claude cannot give it.
CLAUDE_REASONING_EFFORTS: Final[frozenset[str]] = frozenset(
    {"low", "medium", "high", "xhigh", "max"}
)

# The system messages whose session id is a passing thing rather than the conversation's.
# Hook messages carry the id of the hook's own run, and taking one for the cursor would
# leave this conversation resuming from a session that was never its own.
TRANSIENT_SESSION_ID_SYSTEM_SUBTYPES: Final[frozenset[str]] = frozenset(
    {"hook_started", "hook_progress", "hook_response"}
)

# What the CLI calls a turn that somebody stopped. Anything else it reports is a turn that
# ran and stopped on its own account.
ABORTED_TERMINAL_REASONS: Final[frozenset[str]] = frozenset({"aborted_streaming", "aborted_tools"})

# Enough of a dead child's standard error to say what happened, in the failed turn's line
# and in the failure that names why a session would not load.
STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS: Final = 8192

# Tool results can include the contents of a file Claude read. Keep the SDK's line buffer
# bounded while allowing results larger than its 1 MiB default to reach the conversation.
CLAUDE_SDK_MAX_BUFFER_SIZE: Final[int] = 4 * 1024 * 1024

# The three answers this adapter offers for a permission ask, which are the three the SDK's
# callback can give back: allow it this once, allow it and take the SDK's own suggested
# permission updates so it is not asked again this session, or refuse it.
APPROVE_ONCE_OPTION_ID: Final = "approve_once"
ALWAYS_ALLOW_THIS_SESSION_OPTION_ID: Final = "always_allow_this_session"
DECLINE_OPTION_ID: Final = "decline"

PERMISSION_ASK_OPTIONS: Final[tuple[PermissionAskOption, ...]] = (
    PermissionAskOption(
        option_id=APPROVE_ONCE_OPTION_ID, label="Approve once", option_kind="allow_once"
    ),
    PermissionAskOption(
        option_id=ALWAYS_ALLOW_THIS_SESSION_OPTION_ID,
        label="Always allow this session",
        option_kind="allow_always",
    ),
    PermissionAskOption(option_id=DECLINE_OPTION_ID, label="Decline", option_kind="reject_once"),
)

DECLINED_TOOL_MESSAGE: Final = "User declined tool execution."
WITHDRAWN_TOOL_MESSAGE: Final = "The turn ended before this was answered."

# The tool claude uses when it is blocked on a decision that is the owner's to make. It is
# not a permission at all — it is a question, and the answers on offer are the question's own
# choices rather than the three a permission ask has.
ASK_USER_QUESTION_TOOL_NAME: Final = "AskUserQuestion"

# What one of a question's own choices is, as against an allow or a reject. Surfaces read
# this to tell a question from a permission: an ask whose options commit to nothing is one
# the owner answers rather than approves.
QUESTION_CHOICE_OPTION_KIND: Final = "choice"

# Where the tool takes the owner's answers: its own input has a place for them, keyed by the
# full text of the question each one answers. The CLI fills that place from the permission
# result and hands the tool the answers as if they had been collected by its own dialog.
USER_ANSWERS_INPUT_FIELD: Final = "answers"


@dataclass(frozen=True, slots=True)
class ClaudeAgentSdkChildLaunch:
    """Which Claude Code executable to run the child with, and what to run it with.

    ``claude_executable`` of ``None`` leaves the SDK to find one, which is the copy it
    ships with. Production names the installed CLI instead, so the agent a conversation
    runs on is the one on this machine — the version the owner has, logged in the way the
    owner logged it in.

    ``environment_overrides`` are facts about this machine rather than about the
    conversation, so the conversation's own identity variables are put over them.
    """

    claude_executable: Path | None = None
    environment_overrides: tuple[tuple[str, str], ...] = ()


class ClaudeSdkClient(Protocol):
    """The part of ``ClaudeSDKClient`` this adapter uses, and nothing else.

    It is named here so a test can hand the adapter a client it scripts, and so the whole
    of what this adapter asks of the SDK is one thing to look at: connect, prompt, read,
    interrupt, disconnect, and what the child said about itself when it came up.
    """

    async def connect(self) -> None: ...

    async def get_server_info(self) -> dict[str, Any] | None:
        """What the child answered the startup handshake with, exactly as it came.

        ``connect`` performs that handshake, so this is already on the client by the time
        the session is bound: reading it asks the child nothing and sends no prompt. The
        SDK keeps the answer as the raw dictionary it arrived as, which is why it is a
        dictionary here rather than something typed.
        """
        ...

    async def query(self, prompt: str | AsyncIterable[dict[str, Any]]) -> None:
        """A message as words, or as the content blocks a richer one is made of.

        Both are the SDK's own input shapes. Words stay words: the SDK wraps a string in
        exactly the envelope this adapter would otherwise build, so an ordinary prompt is
        untouched by the existence of the other form.
        """
        ...

    def receive_messages(self) -> AsyncIterator[Message]: ...

    async def interrupt(self) -> None: ...

    async def disconnect(self) -> None: ...


type ClaudeSdkClientFactory = Callable[[ClaudeAgentOptions], ClaudeSdkClient]


def claude_sdk_client(options: ClaudeAgentOptions) -> ClaudeSdkClient:
    """The real SDK client for these options."""
    return ClaudeSDKClient(options)


@dataclass(frozen=True, slots=True)
class _QuestionChoice:
    """One of the answers a question offers, and what choosing it would mean."""

    label: str
    description: str


@dataclass(frozen=True, slots=True)
class _UserQuestion:
    """A question claude is blocked on, as it asked it.

    ``text`` is the whole question, and it is also the key the answer goes back under, so it
    is kept rather than reduced to something shorter. ``header`` is the short chip claude
    labelled it with, which is worth showing but is not the question.
    """

    text: str
    header: str
    choices: tuple[_QuestionChoice, ...]
    multi_select: bool


@dataclass(slots=True)
class _ParkedPermissionAsk:
    """One ask waiting for an answer, and the callback that is held open for it.

    ``answer`` is what the callback is waiting on and ``handed_over`` is how the answer's
    sender learns the callback took it. The call being asked about is kept because the
    answer is made out of it: an allow goes back with the call's own input, and a
    session-wide allow with the scope the SDK suggested for it.

    ``question`` is set when the call was claude asking the owner something rather than
    asking to do something. It changes what an answer means — a chosen option is the
    owner's answer to the question, not a permission — so it is kept with the ask.
    """

    answer: asyncio.Future[PermissionResult]
    handed_over: asyncio.Future[None]
    tool_input: dict[str, Any]
    suggestions: tuple[Any, ...]


@dataclass(slots=True)
class _ParkedUserInput:
    answer: asyncio.Future[PermissionResult]
    handed_over: asyncio.Future[None]
    tool_input: dict[str, Any]
    questions: tuple[_UserQuestion, ...]


@dataclass(slots=True)
class _TurnInFlight:
    """The turn this child is running, and what it has half-said so far."""

    token: TurnToken
    cancel_requested: bool = False
    parked_asks: dict[str, _ParkedPermissionAsk] = field(default_factory=dict)
    parked_user_inputs: dict[str, _ParkedUserInput] = field(default_factory=dict)
    user_input_tool_use_ids: set[str] = field(default_factory=set)


class ClaudeAgentSdkBackendChild:
    """One claude child, under one conversation, and its message stream."""

    def __init__(
        self,
        *,
        launch: ClaudeAgentSdkChildLaunch,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
        client_factory: ClaudeSdkClientFactory = claude_sdk_client,
    ) -> None:
        self._launch = launch
        self._resolved_start = resolved_start
        self._sink = event_sink
        self._message_files = message_files
        self._client_factory = client_factory
        self._client: ClaudeSdkClient | None = None
        self._session_id: str | None = None
        self._resume_confirmed = False
        self._session_model: str | None = None
        self._session_reasoning_effort: str | None = None
        self._turn: _TurnInFlight | None = None
        self._last_ended_turn: _TurnInFlight | None = None
        self._reader: asyncio.Task[None] | None = None
        self._wire_broken = False
        self._standard_error: deque[str] = deque()
        self._asks_raised = 0

    # --- the seam -----------------------------------------------------------------------

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        """Spawn claude and bind the session this conversation runs in.

        A fresh session is started under an id minted here and reported before this
        returns, so the conversation can resume from it whatever happens next. A resume
        names the session it wants and gets that one or nothing.
        """
        self._resolved_start = resolved_start
        minted_session_id = None if vendor_session_cursor is not None else str(uuid.uuid4())
        options = self._options(resolved_start, vendor_session_cursor, minted_session_id)
        client = self._client_factory(options)
        await self._connect(client, resumed_from=vendor_session_cursor)
        self._client = client
        self._session_id = vendor_session_cursor or minted_session_id
        # A session claude was asked to load has not proved itself yet; one it was asked to
        # create under our own id is bound to that id from the start.
        self._resume_confirmed = vendor_session_cursor is None
        self._session_model = resolved_start.model
        self._session_reasoning_effort = resolved_start.reasoning_effort
        self._reader = asyncio.create_task(
            self._read_the_stream(client),
            name=f"planner.conversation.claude.{resolved_start.conversation_id}",
        )
        if minted_session_id is not None:
            await self._sink.vendor_session_cursor_rebound(minted_session_id)
        await self._report_the_available_commands(client)

    async def _report_the_available_commands(self, client: ClaudeSdkClient) -> None:
        """Tell the core what a person may type at this child, from its startup handshake.

        The list is the CLI's own answer for the folder this child was started in, so a
        project's own commands are in it because the child was spawned there. That is why
        it is read off the live client: any other copy of claude would be answering about
        somewhere else.

        A child that said nothing about commands leaves the menu alone rather than
        replacing it with an empty one, because having no commands and never having said
        is not the same thing.
        """
        composer_catalog = _composer_catalog_from_the_handshake(await client.get_server_info())
        if composer_catalog is None:
            return
        await self._sink.composer_catalog_reported(composer_catalog)

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
        """Start a turn with this message, on values this child is already running.

        The label and the mode are dropped: the SDK's wire carries a user message and
        nothing alongside it, so there is nowhere for a backend's own metadata to go. The
        turn runs the same either way, which is what the seam says of a backend with no
        such channel.

        A carried change cannot be made to a child that is already running, so it is never
        half-made here: the adapter asks for a rebind before it writes anything, and the
        child that is written to is one that was started on the new values.
        """
        del sender_content, sender_label, mode, automatic_compaction
        self._require_the_carried_values_are_in_force(model_change, reasoning_effort_change)
        client = self._connected_client()
        self._require_a_live_wire()
        asked = await self._query_argument(content)
        try:
            await client.query(asked)
        except Exception as did_not_reach:
            self._wire_broken = True
            raise PromptWriteFailed(str(did_not_reach)) from did_not_reach
        self._turn = _TurnInFlight(token=turn_token)

    async def _query_argument(
        self, content: MessageContent
    ) -> str | AsyncIterator[dict[str, Any]]:
        """The message in the form the SDK takes it.

        A message that is only words stays a string. That is not an optimisation — the SDK
        wraps a string in exactly the envelope it would build here, so keeping the string
        keeps every ordinary prompt byte for byte the thing it has always been, and the
        richer form is reached only by messages that need it.

        A message with more in it goes as one user message carrying content blocks, which
        is the SDK's other documented input.
        """
        if len(content) == 1 and isinstance(content[0], MessageText):
            return content[0].text

        blocks: list[dict[str, Any]] = []
        for piece in content:
            match piece:
                case MessageText():
                    blocks.append({"type": "text", "text": piece.text})
                case MessageImage():
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": piece.media_type,
                                "data": await self._encoded_bytes(piece.stored_file_id),
                            },
                        }
                    )
                case MessageFile():
                    blocks.append(
                        {
                            "type": "text",
                            "text": self._file_context(piece),
                        }
                    )

        async def one_user_message() -> AsyncIterator[dict[str, Any]]:
            yield {
                "type": "user",
                "message": {"role": "user", "content": blocks},
                "parent_tool_use_id": None,
            }

        return one_user_message()

    def _file_context(self, piece: MessageFile) -> str:
        try:
            path = self._message_files.path_of(
                self._resolved_start.conversation_id, piece.stored_file_id
            )
        except MessageFileMissing as unreadable:
            raise PromptWriteFailed(
                f"{piece.stored_file_id} could not be read"
            ) from unreadable
        return (
            f'Attached file "{piece.file_name}" ({piece.media_type}, '
            f"{piece.byte_count} bytes) is available at {path}."
        )

    async def _encoded_bytes(self, stored_file_id: str) -> str:
        """The bytes of a kept file, as the SDK's image block wants them."""
        try:
            kept = await self._message_files.read(
                self._resolved_start.conversation_id, stored_file_id
            )
        except (MessageFileMissing, OSError) as unreadable:
            raise PromptWriteFailed(f"{stored_file_id} could not be read") from unreadable
        return b64encode(kept).decode("ascii")

    async def steer(self, content: MessageContent, *, sender_label: str) -> None:
        """Claude has no way to take text into a turn that is already running."""
        del content, sender_label
        raise PromptWriteFailed("claude cannot take text into a turn that is already running")

    async def cancel_running_turn(self) -> None:
        """Stop the turn that is running. The client, its child and its session stay up."""
        turn = self._turn
        if turn is None:
            return
        # The interrupt is this adapter's own doing, so the ending it produces is known to
        # be an interruption whatever the child goes on to report about it.
        turn.cancel_requested = True
        client = self._connected_client()
        try:
            await client.interrupt()
        except Exception as did_not_reach:
            self._wire_broken = True
            raise PromptWriteFailed(str(did_not_reach)) from did_not_reach
        # The core records the interruption as soon as this returns. Settle every callback
        # at the same boundary instead of waiting for a terminal result Claude may delay.
        self._settle_parked_asks(turn)

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        """Give the SDK the option a person chose, and wait for its callback to take it.

        The answer is what the held-open callback returns, so it has been given only once
        the callback has taken it — which is the difference between an answer that was
        recorded and one that was actually given.
        """
        turn = self._turn
        parked = None if turn is None else turn.parked_asks.get(ask_id)
        if parked is None or parked.answer.done():
            raise PermissionAnswerWriteFailed(ask_id)
        answer = _permission_answer_for(option_id, parked.tool_input, parked.suggestions)
        if answer is None:
            raise PermissionAnswerWriteFailed(f"{ask_id} was not offered {option_id!r}")
        if turn is not None:
            turn.parked_asks.pop(ask_id, None)
        parked.answer.set_result(answer)
        try:
            await parked.handed_over
        except Exception as never_given:
            raise PermissionAnswerWriteFailed(ask_id) from never_given

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        """Give Claude's held callback its complete answer map."""
        turn = self._turn
        parked = None if turn is None else turn.parked_user_inputs.get(request_id)
        if parked is None or parked.answer.done():
            raise UserInputAnswerWriteFailed(request_id)
        answer = _user_input_answer_for(answers, parked.tool_input, parked.questions)
        if answer is None:
            raise UserInputAnswerWriteFailed(f"{request_id} did not receive complete answers")
        if turn is not None:
            turn.parked_user_inputs.pop(request_id, None)
        parked.answer.set_result(answer)
        try:
            await parked.handed_over
        except Exception as never_given:
            raise UserInputAnswerWriteFailed(request_id) from never_given

    async def stop(self) -> None:
        """Shut the child down: stop reading, settle its asks, close the client."""
        turn = self._turn
        self._turn = None
        self._last_ended_turn = None
        if turn is not None:
            self._settle_parked_asks(turn)
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
        client = self._client
        self._client = None
        if client is not None:
            # A client whose child has already gone makes closing it fail too, and a child
            # being shut down has no use for the complaint.
            with suppress(Exception):
                await client.disconnect()

    # --- starting -----------------------------------------------------------------------

    def _options(
        self,
        resolved_start: ResolvedConversationStart,
        vendor_session_cursor: str | None,
        minted_session_id: str | None,
    ) -> ClaudeAgentOptions:
        """Everything the child is started with, from the values this conversation runs on."""
        return ClaudeAgentOptions(
            cwd=str(resolved_start.workspace_folder),
            cli_path=self._launch.claude_executable,
            system_prompt=CLAUDE_CODE_SYSTEM_PROMPT,
            model=resolved_start.model,
            effort=self._effort(resolved_start.reasoning_effort),
            permission_mode=_permission_mode(resolved_start.access),
            resume=vendor_session_cursor,
            session_id=minted_session_id,
            env={
                **dict(self._launch.environment_overrides),
                **dict(_identity_environment(resolved_start)),
            },
            max_buffer_size=CLAUDE_SDK_MAX_BUFFER_SIZE,
            include_partial_messages=True,
            can_use_tool=self._can_use_tool,
            stderr=self._note_standard_error,
        )

    def _effort(self, reasoning_effort: str | None) -> EffortLevel | None:
        """The reasoning effort as claude names it, or nothing this session can be put on.

        A session that cannot be put on the value this conversation runs on is not one to
        write under: the text would go to an agent thinking as hard as somebody else asked.
        """
        if reasoning_effort is None:
            return None
        if reasoning_effort not in CLAUDE_REASONING_EFFORTS:
            raise SessionLoadFailed(
                f"claude has no reasoning effort {reasoning_effort!r}, so a session cannot "
                "be put on it"
            )
        return cast(EffortLevel, reasoning_effort)

    async def _connect(self, client: ClaudeSdkClient, *, resumed_from: str | None) -> None:
        """Bring the child up, or say which of the two things went wrong.

        A resume is the CLI's own startup step: it refuses to come up at all when it does
        not have the session, so a child that would not start under a cursor is a session
        that did not load rather than a process that would not spawn. What the CLI said
        about it is on its standard error, and it is carried into the failure verbatim.
        """
        try:
            with warnings.catch_warnings():
                # A permission mode that allows everything does shadow the ask callback,
                # and both are meant: the mode is the conversation's access posture and the
                # callback is there for the asks that reach it anyway.
                warnings.simplefilter("ignore", CanUseToolShadowedWarning)
                await client.connect()
        except CLINotFoundError as no_executable:
            raise BackendSpawnFailed(str(no_executable)) from no_executable
        except Exception as would_not_come_up:
            why = self._with_the_standard_error(str(would_not_come_up))
            if resumed_from is not None:
                raise SessionLoadFailed(why) from would_not_come_up
            raise BackendSpawnFailed(why) from would_not_come_up

    # --- model and reasoning effort ------------------------------------------------------

    def _require_the_carried_values_are_in_force(
        self, model_change: str | None, reasoning_effort_change: str | None
    ) -> None:
        """Ask for a new child unless this one is already running on what was asked for.

        Both are options of the CLI process, so there is no changing them under a child
        that is up. Saying so before anything is written is what keeps a change and its
        prompt one act: the child that takes the prompt was started on the new values.
        """
        if model_change is not None and model_change != self._session_model:
            raise NeedsRebind(
                f"claude takes its model when the child starts, so {model_change!r} needs "
                "a child started on it"
            )
        if (
            reasoning_effort_change is not None
            and reasoning_effort_change != self._session_reasoning_effort
        ):
            raise NeedsRebind(
                "claude takes its reasoning effort when the child starts, so "
                f"{reasoning_effort_change!r} needs a child started on it"
            )

    # --- writing ------------------------------------------------------------------------

    def _connected_client(self) -> ClaudeSdkClient:
        client = self._client
        if client is None:
            raise PromptWriteFailed("this child has no bound session")
        return client

    def _require_a_live_wire(self) -> None:
        """Replace a known-broken wire before a new prompt writes any bytes."""
        if self._wire_broken:
            raise NeedsRebind(
                "this child's wire has already failed", failed_child_recovery=True
            )

    # --- the turn -----------------------------------------------------------------------

    async def _end_turn(
        self,
        turn: _TurnInFlight,
        ending: ConversationTurnEnding,
        error_summary: str | None,
    ) -> None:
        if self._turn is not turn:
            return
        self._turn = None
        self._last_ended_turn = turn
        self._settle_parked_asks(turn)
        await self._sink.turn_ended(
            turn.token,
            ending=ending,
            error_summary=error_summary,
            standard_error_tail=(
                self._standard_error_tail() if ending is ConversationTurnEnding.failed else None
            ),
        )

    async def _fail_the_running_turn(self, why: str) -> None:
        turn = self._turn
        if turn is not None:
            await self._end_turn(turn, ConversationTurnEnding.failed, why)

    def _settle_parked_asks(self, turn: _TurnInFlight) -> None:
        """A turn's asks die with it, and the SDK is told so rather than left waiting."""
        for parked in list(turn.parked_asks.values()):
            if not parked.answer.done():
                parked.answer.set_result(PermissionResultDeny(message=WITHDRAWN_TOOL_MESSAGE))
        turn.parked_asks.clear()
        for parked_user_input in list(turn.parked_user_inputs.values()):
            if not parked_user_input.answer.done():
                parked_user_input.answer.set_result(
                    PermissionResultDeny(message=WITHDRAWN_TOOL_MESSAGE)
                )
        turn.parked_user_inputs.clear()

    # --- what the agent says -------------------------------------------------------------

    async def _read_the_stream(self, client: ClaudeSdkClient) -> None:
        """Work through the child's messages until it has no more to say."""
        try:
            async for message in client.receive_messages():
                await self._take_in(message)
        except asyncio.CancelledError:
            raise
        except Exception as stream_failed:
            self._wire_broken = True
            await self._fail_the_running_turn(self._with_the_standard_error(str(stream_failed)))
        else:
            self._wire_broken = True
            await self._fail_the_running_turn(
                self._with_the_standard_error("the backend's message stream ended")
            )

    async def _take_in(self, message: Message) -> None:
        """One piece of the agent's news, turned into what the core keeps or shows."""
        session_id = _durable_session_id(message)
        if session_id is not None:
            await self._note_the_session_id(session_id)
        if self._wire_broken:
            return
        turn = self._turn
        if turn is None:
            # Claude's persistent run may deliver the parent's next ordinary message after
            # a result ended the backend turn. It is still a message in this conversation,
            # so preserve it under the turn that just ended. Every other late event remains
            # turn-bound and is dropped. The session id above is conversation-bound too.
            if isinstance(message, AssistantMessage) and self._last_ended_turn is not None:
                await self._on_assistant_message_after_turn(self._last_ended_turn, message)
            return
        match message:
            case StreamEvent():
                await self._on_stream_event(turn, message)
            case AssistantMessage():
                await self._on_assistant_message(turn, message)
            case UserMessage():
                await self._on_user_message(turn, message)
            case ResultMessage():
                await self._on_result_message(turn, message)
            case SystemMessage(subtype="compact_boundary"):
                # Claude summarised the conversation so far and dropped what it summarised.
                # What it summarised is not kept and there is nothing to keep: that it
                # happened, and where in the thread, is the whole of what a reader needs.
                await self._sink.context_compacted(turn.token)
            case _:
                LOGGER.debug(
                    "conversation %s: nothing to do with a %s",
                    self._resolved_start.conversation_id,
                    type(message).__name__,
                )

    async def _note_the_session_id(self, session_id: str) -> None:
        """Keep the session this conversation resumes from, and refuse a substitute.

        Until a resumed session has answered under the id it was asked for there is no
        knowing it is the right one, so an id that differs is taken for what it would be:
        another thread standing in for this conversation's own. After that, an id that
        changes is claude moving the session, and the conversation follows it.
        """
        if session_id == self._session_id:
            self._resume_confirmed = True
            return
        if not self._resume_confirmed:
            await self._refuse_the_substituted_session(session_id)
            return
        self._session_id = session_id
        await self._sink.vendor_session_cursor_rebound(session_id)

    async def _refuse_the_substituted_session(self, session_id: str) -> None:
        """Have nothing more to do with a child that is not the session that was asked for.

        The cursor is left alone — writing this session down would be the silent acceptance
        this exists to prevent — and the child is finished with, so the next message starts
        one that asks for the conversation's own session again.
        """
        self._wire_broken = True
        await self._fail_the_running_turn(
            f"claude answered under session {session_id!r} instead of the session "
            f"{self._session_id!r} this conversation asked to resume, so its memory of "
            "this conversation is not there"
        )

    async def _on_stream_event(self, turn: _TurnInFlight, message: StreamEvent) -> None:
        """The half-finished text of a message, shown live and then forgotten.

        Text written inside a tool call belongs to that call rather than to the agent: a
        subagent talking is what the call is doing while it runs. It is shown against the
        call it came from, and it is just as ephemeral as the agent's own half-finished
        text — the call's result is what the record keeps.
        """
        event = message.event
        if event.get("type") != "content_block_delta":
            return
        delta = event.get("delta")
        if not isinstance(delta, dict):
            return
        # What the model thought is dropped where it arrives, along with everything else
        # that is not the agent's own text. That it thought is forwarded on its own.
        if delta.get("type") == "thinking_delta":
            await self._sink.model_thinking_happened(turn.token)
            return
        if delta.get("type") != "text_delta":
            return
        text = delta.get("text")
        if not isinstance(text, str) or not text:
            return
        inside_a_tool_call = message.parent_tool_use_id
        if inside_a_tool_call is not None:
            await self._sink.tool_call_progress(
                turn.token, tool_call_id=inside_a_tool_call, detail=text
            )
            return
        await self._sink.agent_message_delta(turn.token, text)

    async def _on_assistant_message(self, turn: _TurnInFlight, message: AssistantMessage) -> None:
        """A finished message from the agent, and the tool calls it made in it.

        The blocks are taken in the order they were written, and text is finished off
        whenever a tool call interrupts it, so a message that says something, works, and
        then says more is recorded as that rather than as one run-on message.
        """
        if message.parent_tool_use_id is not None:
            # A subagent talking inside a tool call. The tool call is the thing that
            # happened; its inner conversation is not the agent's message to anyone.
            return
        said: list[str] = []
        for block in message.content:
            match block:
                case TextBlock():
                    said.append(block.text)
                case ToolUseBlock():
                    await self._complete_agent_message(turn, said)
                    if block.name == ASK_USER_QUESTION_TOOL_NAME:
                        turn.user_input_tool_use_ids.add(block.id)
                        continue
                    plan = _todo_write_plan(block)
                    if plan is not None:
                        await self._sink.plan_updated(turn.token, plan)
                    await self._sink.tool_call_started(
                        turn.token,
                        tool_call_id=block.id,
                        title=block.name,
                        # Claude classifies its calls by the tool they are made on: every
                        # Bash call is the same kind of thing. There is no other kind on
                        # the wire, and the tool's name is a truer one than "other".
                        tool_kind=block.name,
                        detail=_canonical_json(block.input),
                    )
                case ThinkingBlock():
                    # What it thought is dropped where it arrives: never stored, never
                    # forwarded. That it thought is forwarded, and is all that is.
                    await self._sink.model_thinking_happened(turn.token)
                    continue
                case _:
                    continue
        await self._complete_agent_message(turn, said)

    async def _on_assistant_message_after_turn(
        self, turn: _TurnInFlight, message: AssistantMessage
    ) -> None:
        """Keep a plain parent message that Claude delivered after its result.

        Tool activity, subagent speech, thinking pulses and streaming decoration remain
        facts of a live turn. Only a completed ordinary top-level message crosses this
        boundary; it is already whole and belongs in the durable conversation record.
        """
        if message.parent_tool_use_id is not None:
            return
        said: list[str] = []
        for block in message.content:
            if isinstance(block, TextBlock):
                said.append(block.text)
            elif isinstance(block, ThinkingBlock):
                continue
            else:
                return
        await self._complete_agent_message(turn, said)

    async def _complete_agent_message(self, turn: _TurnInFlight, said: list[str]) -> None:
        if not said:
            return
        text = "".join(said)
        said.clear()
        await self._sink.agent_message_completed(turn.token, text_message_content(text))

    async def _on_user_message(self, turn: _TurnInFlight, message: UserMessage) -> None:
        """What the tools gave back. Their results arrive as a message from the user side.

        Results from inside a tool call are that call's own business. The tool calls a
        subagent makes are never reported as started — they belong to its inner
        conversation, not to this one — so reporting their finishes would be telling the
        record that calls it never saw have ended.
        """
        if message.parent_tool_use_id is not None:
            return
        content = message.content
        if isinstance(content, str):
            return
        for block in content:
            if not isinstance(block, ToolResultBlock):
                continue
            if block.tool_use_id in turn.user_input_tool_use_ids:
                continue
            await self._sink.tool_call_finished(
                turn.token,
                tool_call_id=block.tool_use_id,
                tool_call_status=(
                    ToolCallStatus.failed if block.is_error else ToolCallStatus.completed
                ),
                detail=_tool_result_detail(block.content),
            )

    async def _on_result_message(self, turn: _TurnInFlight, message: ResultMessage) -> None:
        """The turn stopped running, and this says how — and what has been spent.

        Two things say it was stopped rather than finished, and both are facts rather than
        readings of an error's wording: the CLI's own name for a turn that was aborted, and
        this adapter having asked for the interrupt itself.
        """
        await self._report_what_has_been_spent(turn, message)
        if turn.cancel_requested or message.terminal_reason in ABORTED_TERMINAL_REASONS:
            await self._end_turn(turn, ConversationTurnEnding.interrupted, None)
            return
        if message.is_error:
            await self._end_turn(
                turn, ConversationTurnEnding.failed, _result_error_summary(message)
            )
            return
        await self._end_turn(turn, ConversationTurnEnding.completed, None)

    async def _report_what_has_been_spent(
        self, turn: _TurnInFlight, message: ResultMessage
    ) -> None:
        """The tokens and the money, exactly as claude counts them.

        Claude counts by the session rather than by the turn: every request is added into
        one running tally for the child, and that tally is what goes on every result message
        it sends. So these are totals for the conversation so far, which is what the seam
        asks for from a backend that reports running totals.

        ``total_cost_usd`` is the money, because that is the number claude itself calls the
        cost. The per-model ``costUSD`` figures beside it are that same total split by which
        model earned it, and adding them back up would be arriving at the number claude has
        already given.

        It is reported before the ending because the ending is what stops this turn being
        the running one, and news about a turn that is over is dropped.

        A count claude did not give is nothing rather than zero: a result message that says
        nothing about cached tokens has not said the turn read none.
        """
        counted = message.usage or {}
        await self._sink.token_usage_reported(
            turn.token,
            input_tokens=_token_count(counted.get("input_tokens")),
            output_tokens=_token_count(counted.get("output_tokens")),
            cached_input_tokens=_token_count(counted.get("cache_read_input_tokens")),
            cost_usd=message.total_cost_usd,
        )

    # --- permission asks -------------------------------------------------------------------

    async def _can_use_tool(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResult:
        """Hold the agent's ask open until a person answers it or its turn dies.

        Two different things arrive here. Most are claude asking to *do* something, and the
        answers are the three a permission has. One is claude asking the owner a question it
        cannot answer itself, and then the answers are the question's own choices — raising
        that as a permission would show the owner a tool call to approve instead of the
        question they were asked, and approving it would run the tool with nothing chosen.
        """
        turn = self._turn
        if turn is None:
            return PermissionResultDeny(message=WITHDRAWN_TOOL_MESSAGE)
        self._asks_raised += 1
        ask_id = f"{context.tool_use_id or tool_name}:{self._asks_raised}"
        if tool_name == ASK_USER_QUESTION_TOOL_NAME:
            questions = _user_questions(tool_input)
            if questions is None:
                await self._sink.user_input_failed(
                    turn.token,
                    request_id=ask_id,
                    detail="Claude sent a malformed question request, so Panels refused it.",
                )
                return PermissionResultDeny(
                    message="The question request was malformed and could not be shown."
                )
            loop = asyncio.get_running_loop()
            parked_user_input = _ParkedUserInput(
                answer=loop.create_future(),
                handed_over=loop.create_future(),
                tool_input=dict(tool_input),
                questions=questions,
            )
            turn.parked_user_inputs[ask_id] = parked_user_input
            if context.tool_use_id is not None:
                turn.user_input_tool_use_ids.add(context.tool_use_id)
            await self._sink.user_input_requested(
                turn.token,
                BackendUserInputRequest(
                    request_id=ask_id,
                    questions=tuple(
                        UserInputQuestion(
                            question_id=question.text,
                            header=question.header,
                            question=question.text,
                            options=tuple(
                                UserInputOption(
                                    label=choice.label, description=choice.description
                                )
                                for choice in question.choices
                            ),
                            multi_select=question.multi_select,
                            allow_other=True,
                        )
                        for question in questions
                    ),
                ),
            )
            try:
                answer = await parked_user_input.answer
            except BaseException as never_answered:
                if not parked_user_input.handed_over.done():
                    parked_user_input.handed_over.set_exception(
                        UserInputAnswerWriteFailed(str(never_answered))
                    )
                    parked_user_input.handed_over.add_done_callback(
                        lambda settled: settled.exception()
                    )
                raise
            if not parked_user_input.handed_over.done():
                parked_user_input.handed_over.set_result(None)
            return answer

        loop = asyncio.get_running_loop()
        parked = _ParkedPermissionAsk(
            answer=loop.create_future(),
            handed_over=loop.create_future(),
            tool_input=dict(tool_input),
            suggestions=tuple(context.suggestions),
        )
        turn.parked_asks[ask_id] = parked
        await self._sink.permission_ask_raised(
            turn.token,
            BackendPermissionAsk(
                ask_id=ask_id,
                title=tool_name,
                detail=_canonical_json(tool_input),
                options=PERMISSION_ASK_OPTIONS,
            ),
        )
        try:
            answer = await parked.answer
        except BaseException as never_answered:
            if not parked.handed_over.done():
                parked.handed_over.set_exception(PermissionAnswerWriteFailed(str(never_answered)))
                # There may be nobody waiting to hear it. Reading it back keeps a failure
                # that has already been dealt with from being reported as one that was not.
                parked.handed_over.add_done_callback(lambda settled: settled.exception())
            raise
        if not parked.handed_over.done():
            parked.handed_over.set_result(None)
        return answer

    # --- the child's own noise ------------------------------------------------------------

    def _note_standard_error(self, line: str) -> None:
        """Keep the last of what the child complained about, and no more than that.

        A child that runs all day writes more than anyone will read, so what is kept is
        bounded by what a failure's line can carry rather than by how long it ran.
        """
        self._standard_error.append(line)
        while (
            sum(len(part) for part in self._standard_error) > STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS
            and len(self._standard_error) > 1
        ):
            self._standard_error.popleft()

    def _with_the_standard_error(self, why: str) -> str:
        tail = self._standard_error_tail()
        return why if tail is None else f"{why}: {tail}"

    def _standard_error_tail(self) -> str | None:
        if not self._standard_error:
            return None
        tail = "".join(self._standard_error)[-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS:]
        return tail.strip() or None


class ClaudeAgentSdkBackendChildFactory:
    """Makes the claude child for one conversation. Making it does not spawn it."""

    def __init__(
        self,
        launch: ClaudeAgentSdkChildLaunch,
        *,
        client_factory: ClaudeSdkClientFactory = claude_sdk_client,
    ) -> None:
        self._launch = launch
        self._client_factory = client_factory

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> ClaudeAgentSdkBackendChild:
        return ClaudeAgentSdkBackendChild(
            launch=self._launch,
            resolved_start=resolved_start,
            event_sink=event_sink,
            message_files=message_files,
            client_factory=self._client_factory,
        )


def _permission_mode(access: ConversationAccess) -> PermissionMode:
    """How an access posture is realized as one of claude's permission modes.

    ``full`` is claude's own everything-allowed mode. When a second posture is ruled it is
    added here, next to the one it differs from.
    """
    if access is ConversationAccess.full:
        return "bypassPermissions"
    return "default"


def _identity_environment(
    resolved_start: ResolvedConversationStart,
) -> tuple[tuple[str, str], ...]:
    """The conversation's own answer to who this agent is.

    These are put over the environment this process runs in, which the child otherwise
    inherits whole. Nothing else is added here — least of all ``HOME``.
    """
    role_materials = resolved_start.role_materials
    if role_materials is None:
        return ()
    return role_materials.identity_environment_variables


def _composer_catalog_from_the_handshake(
    handshake: dict[str, Any] | None,
) -> tuple[ComposerCatalogEntry, ...] | None:
    """The commands the child said it takes, or nothing when it said nothing about them.

    The handshake is raw wire data — the SDK hands it over as it came, with no model of
    its own for what is in it — so every piece is checked rather than trusted. An entry
    with no name is a command nobody could type, and it is left out while the rest of the
    list stands: a menu missing one line is worth more than no menu at all.

    An empty list back is claude saying it has no commands, which is an answer. A
    handshake that carried no commands at all is claude saying nothing, and that is the
    ``None``.
    """
    if handshake is None:
        return None
    listed = handshake.get("commands")
    if not isinstance(listed, list):
        return None
    commands: list[ComposerCatalogEntry] = []
    for entry in listed:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        description = entry.get("description")
        # The CLI's own spelling, kept because the Python SDK does no renaming on the way
        # through. ``aliases`` arrives beside these and is deliberately left there: a
        # command has one name in this system, and offering its other spellings would be
        # offering the same command several times over.
        argument_hint = entry.get("argumentHint")
        commands.append(
            ComposerCatalogEntry(
                kind=ComposerCatalogEntryKind.command,
                display_text=f"/{name}",
                insertion_text=f"/{name} ",
                description=description if isinstance(description, str) else "",
                argument_hint=(
                    argument_hint if isinstance(argument_hint, str) and argument_hint else None
                ),
            )
        )
    return tuple(commands)


def _durable_session_id(message: Message) -> str | None:
    """The session id this message says the conversation is in, when it says one at all."""
    match message:
        case HookEventMessage():
            return None
        case SystemMessage():
            if message.subtype in TRANSIENT_SESSION_ID_SYSTEM_SUBTYPES:
                return None
            session_id = message.data.get("session_id")
            return session_id if isinstance(session_id, str) else None
        case AssistantMessage() | ResultMessage() | StreamEvent() | RateLimitEvent():
            return message.session_id
        case _:
            return None


def _user_questions(tool_input: dict[str, Any]) -> tuple[_UserQuestion, ...] | None:
    """Strictly parse the whole ordered AskUserQuestion payload or reject all of it."""
    questions = tool_input.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    parsed: list[_UserQuestion] = []
    seen_text: set[str] = set()
    for asked in questions:
        if not isinstance(asked, dict):
            return None
        text = asked.get("question")
        if not isinstance(text, str) or not text or text in seen_text:
            return None
        seen_text.add(text)
        offered = asked.get("options")
        if not isinstance(offered, list) or not offered:
            return None
        choices: list[_QuestionChoice] = []
        for option in offered:
            if not isinstance(option, dict):
                return None
            label = option.get("label")
            description = option.get("description")
            if (
                not isinstance(label, str)
                or not label
                or not isinstance(description, str)
            ):
                return None
            choices.append(_QuestionChoice(label=label, description=description))
        if len({choice.label for choice in choices}) != len(choices):
            return None
        multi_select = asked.get("multiSelect")
        if not isinstance(multi_select, bool):
            return None
        header = asked.get("header")
        if not isinstance(header, str):
            return None
        parsed.append(
            _UserQuestion(
                text=text,
                header=header,
                choices=tuple(choices),
                multi_select=multi_select,
            )
        )
    return tuple(parsed)


def _permission_answer_for(
    option_id: str,
    tool_input: dict[str, Any],
    suggestions: tuple[Any, ...],
) -> PermissionResult | None:
    """Translate one genuine permission decision to Claude's SDK."""
    if option_id == APPROVE_ONCE_OPTION_ID:
        return PermissionResultAllow(updated_input=dict(tool_input))
    if option_id == ALWAYS_ALLOW_THIS_SESSION_OPTION_ID:
        return PermissionResultAllow(
            updated_input=dict(tool_input),
            updated_permissions=list(suggestions) or None,
        )
    if option_id == DECLINE_OPTION_ID:
        return PermissionResultDeny(message=DECLINED_TOOL_MESSAGE)
    return None


def _user_input_answer_for(
    answers: tuple[UserInputAnswer, ...],
    tool_input: dict[str, Any],
    questions: tuple[_UserQuestion, ...],
) -> PermissionResult | None:
    """Fill Claude's original tool input with a complete full-question-text answer map."""
    if tuple(answer.question_id for answer in answers) != tuple(
        question.text for question in questions
    ):
        return None
    answer_map: dict[str, str] = {}
    for question, answer in zip(questions, answers, strict=True):
        if not answer.answers or (not question.multi_select and len(answer.answers) != 1):
            return None
        if any(not value for value in answer.answers):
            return None
        answer_map[question.text] = ", ".join(answer.answers)
    return PermissionResultAllow(
        updated_input={**tool_input, USER_ANSWERS_INPUT_FIELD: answer_map}
    )


def _todo_write_plan(block: ToolUseBlock) -> tuple[PlanEntry, ...] | None:
    """Claude's plan, read off the one tool that carries one.

    Claude has no plan on its wire at all: what it has is a tool it calls to keep its own
    todo list, and the list is the tool's input. So the plan is read from the call as it is
    made — which is also the moment it changes.

    Anything the least bit unexpected reads as no plan rather than a guessed one. A wrong
    plan on the screen is worse than none, and the tool call itself is still recorded
    whatever this decides.
    """
    if block.name != TODO_WRITE_TOOL_NAME or not isinstance(block.input, dict):
        return None
    listed = block.input.get("todos")
    if not isinstance(listed, list) or not listed:
        return None
    entries: list[PlanEntry] = []
    for todo in listed:
        if not isinstance(todo, dict):
            return None
        text = todo.get("content")
        status = todo.get("status")
        if not isinstance(text, str) or not text or not isinstance(status, str):
            return None
        try:
            entries.append(PlanEntry(text=text, status=PlanEntryStatus(status)))
        except ValueError:
            # A status this system has no word for. Showing the plan without it would be
            # showing a different plan.
            return None
    return tuple(entries)


def _token_count(counted: Any) -> int | None:
    """One of claude's token counts, when what it put there was a whole number."""
    return counted if isinstance(counted, int) else None


def _canonical_json(value: Any) -> str | None:
    """A tool's input or a result's payload as one readable text, or nothing to show."""
    if not value:
        return None
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _tool_result_detail(content: str | list[dict[str, Any]] | None) -> str | None:
    """The readable part of a tool result, which is its text when it has any."""
    if content is None:
        return None
    if isinstance(content, str):
        return content or None
    texts = [
        text
        for item in content
        if isinstance(item, dict) and isinstance(text := item.get("text"), str) and text
    ]
    return "\n".join(texts) or None


def _result_error_summary(message: ResultMessage) -> str:
    """What to say about a turn that failed, in the words the CLI used for it."""
    if message.errors:
        return "; ".join(str(error) for error in message.errors)
    if message.result:
        return message.result
    if message.api_error_status is not None:
        return f"{message.subtype} (HTTP {message.api_error_status})"
    return message.subtype
