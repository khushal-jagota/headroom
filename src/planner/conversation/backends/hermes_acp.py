"""Hermes behind the backend seam: one child process, one ACP session, one wire.

This is the whole of what the conversation system knows about talking to hermes. It owns a
subprocess, the Agent Client Protocol connection to it, and the session that conversation
resumes from. It owns none of the conversation's rules: it never decides that a message
waits, never decides that an ask has expired, and never writes a row.

Six things about ACP shape this adapter, and each one is why a piece of it looks the way
it does.

**A prompt's response is the turn's ending, not its acknowledgment.** ``session/prompt``
answers when the turn is over, so waiting for it in ``write_prompt`` would mean a send that
returns minutes later. The prompt request is started, the call returns the moment its bytes
are on the wire, and a background task turns the eventual response into the turn's ending.
That is also why a prompt the agent refuses cannot be a ``PromptWriteFailed``: refusal
arrives long after the write, and the contract says so in as many words.

**A steer is a second prompt.** Hermes takes text into a running turn as another
``session/prompt`` on the same session whose text begins ``/steer ``. Its response is the
steer's own, not the turn's, so it is consumed and dropped.

**Permission asks come the other way.** The agent calls back with
``session/request_permission`` and waits for the answer, so an ask is a request left open
until a person answers it or the turn it belongs to dies.

**Hermes changes model the old way.** It does not advertise the model as an ACP session
config option; it implements the retired ``session/set_model`` method instead. The adapter
uses a session's advertised config option when there is one for that semantic category and
falls back to the legacy method for the model, which is the path real hermes takes. A
reasoning effort has no legacy method, so a hermes session that advertises no
``thought_level`` option cannot be put on one, and the adapter says so rather than running
on a value nobody asked for.

**The commands a person may type arrive unasked, and before any turn.** ACP has no way to
ask an agent what its commands are: hermes pushes them as a session update the moment a
session is established, and again whenever the list changes. So the only place to hear
them is the same handler everything else arrives at, and they have to be answered above
its turn guard — every other update is a piece of some turn's news, and this one is a fact
about the session, sent when there is no turn to attach it to.

**A compaction is hermes' own news, not ACP's.** ACP has no word for the moment an agent
summarises what came before and drops it. Hermes does it, and says so on a session info
update inside its own ``_meta``: it starts a new internal session when it compacts, and
names compression as the reason that one replaced the last. The ACP session this
conversation resumes from is unchanged, so that metadata is the only place the boundary
shows up at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from collections import deque
from collections.abc import Sequence
from contextlib import AsyncExitStack, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from acp import AGENT_METHODS, CLIENT_METHODS, PROTOCOL_VERSION
from acp.client.connection import ClientSideConnection
from acp.connection import StreamDirection, StreamEvent
from acp.exceptions import RequestError
from acp.interfaces import Client
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommand,
    AvailableCommandsUpdate,
    ClientCapabilities,
    ContentToolCallContent,
    DeniedOutcome,
    ImageContentBlock,
    Implementation,
    PermissionOption,
    RequestPermissionResponse,
    SessionConfigOptionSelect,
    SessionInfoUpdate,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    Usage,
    UsageUpdate,
)
from acp.transports import spawn_stdio_transport

from planner import __version__
from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendPermissionAsk,
    BackendSpawnFailed,
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
)
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    MessagePiece,
    MessageText,
    joined_runs_of_text,
    prefix_message_content_text,
)
from planner.conversation.message_files import (
    ConversationMessageFiles,
    MessageFileMissing,
)

LOGGER = logging.getLogger("planner.conversation.backends.hermes_acp")

# What hermes reads as text meant for the turn that is already running.
HERMES_STEER_COMMAND_PREFIX = "/steer "

# The retired ACP method hermes still answers for a mid-session model change.
LEGACY_SET_SESSION_MODEL_METHOD = "session/set_model"

# The semantic categories an ACP session labels its own config options with. They are the
# agent's words, not ours: an option in the ``model`` category is the session's model.
MODEL_CONFIGURATION_CATEGORY = "model"
REASONING_EFFORT_CONFIGURATION_CATEGORY = "thought_level"

# Enough of a dead agent's standard error to say what happened, in the failed turn's line.
STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS = 8192

# How long a cancelled turn is given to finish at the agent before the next message is
# written anyway. A hermes that is going to answer its cancel answers it in well under a
# second; this is only the point at which waiting stops being worth the delay.
CANCELLED_TURN_ENDING_TIMEOUT_SECONDS = 15.0

# How long an answer to a permission ask is given to show up on the wire. The response to a
# held-open request is sent by the SDK once the handler returns, and a send of its own that
# fails is not reported back here — so an answer nobody can show reached hermes is called
# what it is instead of being waited on for good.
ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS = 15.0

# Agent output arrives one JSON line at a time and a finished message can be long, so the
# line limit is raised well past the stream default rather than left to be reassembled.
CHILD_OUTPUT_LINE_LIMIT_BYTES = 50 * 1024 * 1024

# What the ACP tool-call statuses mean to a conversation's record. A tool call that is
# still pending or in progress has not finished, so it is not a finish.
_FINISHED_TOOL_CALL_STATUSES: dict[str, ToolCallStatus] = {
    "completed": ToolCallStatus.completed,
    "failed": ToolCallStatus.failed,
}

# The one stop reason that means somebody stopped the turn rather than the agent finishing
# it. Everything else — the agent ran out of tokens, ran out of turns, declined — is a turn
# that ran and stopped on its own account.
_CANCELLED_STOP_REASON = "cancelled"

# Where in a session info update's ``_meta`` hermes says that its internal session was
# replaced, and the reason it gives when the replacement was a compaction. A session info
# update about anything else — a title it has just written — names no reason at all, which
# is what tells a compaction boundary from an ordinary one.
HERMES_METADATA_KEY = "hermes"
SESSION_PROVENANCE_METADATA_KEY = "sessionProvenance"
SESSION_REPLACEMENT_REASON_KEY = "reason"
COMPACTION_SESSION_REPLACEMENT_REASON = "compression"

# What hermes calls dollars when it states a cost. A cost in anything else is a true
# number with nowhere to go — the record's field is dollars — and converting one at a rate
# nobody supplied would be inventing a figure.
_DOLLAR_CURRENCY_CODES: frozenset[str] = frozenset({"USD"})


class _ConfigurationNotApplied(Exception):
    """The session could not be put on a value it was asked for."""


@dataclass(frozen=True, slots=True)
class AcpChildLaunch:
    """What to run, and what to run it with, to get an ACP agent on a pipe.

    Production builds this from the hermes install with ``hermes_acp_child_launch``. It is
    a value rather than a hard-coded command so that a test can point the same adapter at
    an agent it scripts, and so the whole of what is launched is one thing to look at.
    """

    argv: tuple[str, ...]
    environment_overrides: tuple[tuple[str, str], ...] = ()


def hermes_acp_child_launch(
    *,
    hermes_executable: Path,
    hermes_home: Path,
    hermes_python_source_root: Path,
    panels_server_url: str,
) -> AcpChildLaunch:
    """The launch for the hermes on this machine, with the environment it needs."""
    return AcpChildLaunch(
        argv=(str(hermes_executable), "acp"),
        environment_overrides=(
            ("HERMES_HOME", str(hermes_home)),
            ("HERMES_PYTHON_SRC_ROOT", str(hermes_python_source_root)),
            ("PLAN_SERVER_URL", panels_server_url),
        ),
    )


def _access_environment(access: ConversationAccess) -> tuple[tuple[str, str], ...]:
    """How an access posture is realized in the child's environment.

    ``full`` is hermes' own full-access switch. When a second posture is ruled it is added
    here, next to the one it differs from.
    """
    if access is ConversationAccess.full:
        return (("HERMES_YOLO_MODE", "1"),)
    return ()


# The names a child inherits from this process. It is the ACP SDK's own list, which is the
# list hermes is launched with today: everything else the child needs is passed to it.
INHERITED_ENVIRONMENT_NAMES: tuple[str, ...] = (
    "HOME",
    "LOGNAME",
    "PATH",
    "SHELL",
    "TERM",
    "USER",
)


def _child_environment(
    launch: AcpChildLaunch, resolved_start: ResolvedConversationStart
) -> dict[str, str]:
    """The environment the child runs in: inherited names, then everything asked for.

    The identity variables come last because they are the conversation's own answer to who
    this agent is, and nothing generic should be able to overwrite them.
    """
    environment = {
        name: value
        for name in INHERITED_ENVIRONMENT_NAMES
        if (value := os.environ.get(name)) is not None
    }
    environment.update(launch.environment_overrides)
    environment.update(_access_environment(resolved_start.access))
    role_materials = resolved_start.role_materials
    if role_materials is not None:
        environment.update(role_materials.identity_environment_variables)
    return environment


@dataclass(slots=True)
class _ParkedPermissionAsk:
    """One ask waiting for an answer, and the request that is held open for it."""

    answer: asyncio.Future[RequestPermissionResponse]
    request_id: Any


@dataclass(slots=True)
class _TurnInFlight:
    """The turn this child is running, and what it has half-said so far."""

    token: TurnToken
    prompt: asyncio.Task[Any]
    ending_reporter: asyncio.Task[None] | None = None
    agent_message_pieces: list[MessagePiece] = field(default_factory=list)
    agent_message_id: str | None = None
    parked_asks: dict[str, _ParkedPermissionAsk] = field(default_factory=dict)


class HermesAcpBackendChild:
    """One hermes child process, under one conversation, and its ACP wire."""

    def __init__(
        self,
        *,
        launch: AcpChildLaunch,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> None:
        self._launch = launch
        self._resolved_start = resolved_start
        self._sink = event_sink
        self._message_files = message_files
        self._processes = AsyncExitStack()
        self._connection: ClientSideConnection | None = None
        self._session_id: str | None = None
        self._session_configuration_options: tuple[Any, ...] = ()
        self._session_model: str | None = None
        self._session_reasoning_effort: str | None = None
        self._turn: _TurnInFlight | None = None
        self._wire_broken = False
        self._standard_error: deque[str] = deque()
        self._standard_error_reader: asyncio.Task[None] | None = None
        self._prompt_write_waiters: deque[asyncio.Future[None]] = deque()
        # Held for the whole of taking a piece in and for the whole of handing a finished
        # message over. Keeping a picture's bytes goes to a thread, which lets go of the
        # loop, and the turn's ending can arrive in that gap — so without this a picture
        # at the end of a message is flushed after the message it belongs to has gone.
        self._agent_message_lock = asyncio.Lock()
        self._incoming_permission_request_ids: deque[Any] = deque()
        self._permission_answer_waiters: dict[Any, asyncio.Future[None]] = {}
        self._child_watcher: asyncio.Task[None] | None = None
        self._asks_raised = 0

    # --- the seam -----------------------------------------------------------------------

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        """Spawn hermes, speak ACP to it, and bind the session this conversation runs in.

        A child that does not finish starting is shut down here, by the only thing holding
        it. The core adopts a child when this returns, so one that never returned is one it
        was never given and cannot be asked to stop — leaving a hermes running, its wire
        open and its reader tasks alive, for a conversation that has no child at all.
        """
        self._resolved_start = resolved_start
        try:
            connection = await self._spawn(resolved_start)
            if vendor_session_cursor is None:
                await self._create_session(connection, resolved_start)
            else:
                await self._load_session(connection, resolved_start, vendor_session_cursor)
            await self._apply_start_values(resolved_start)
        except BaseException:
            await self.stop()
            raise

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
        """Put the session on any carried values and start a turn with this text.

        The two are one act. If the prompt does not reach the wire the change is put back;
        when it cannot be put back — because the wire it would go over is the wire that
        just failed, or because the session was on the agent's own default and there is no
        way to name that again — the child as it stands is no longer what the conversation
        is running on, and the honest answer is to start it again.
        """
        del sender_content
        previously = (self._session_model, self._session_reasoning_effort)
        try:
            await self._apply_values(model_change, reasoning_effort_change)
        except _ConfigurationNotApplied as not_applied:
            raise PromptWriteFailed(str(not_applied)) from not_applied

        try:
            prompt = await self._write_prompt_to_the_wire(
                content, sender_label=sender_label, mode=mode
            )
        except PromptWriteFailed:
            if model_change is not None or reasoning_effort_change is not None:
                await self._put_the_values_back(previously)
            raise
        self._begin_turn(turn_token, prompt)

    async def steer(self, content: MessageContent, *, sender_label: str) -> None:
        """Send hermes' steer command, which joins the turn instead of starting one.

        The command word goes in front of the message the way it always did — onto its
        opening words when it has them, and as a piece of its own when the message opens
        with something else, so a steered picture still arrives as a steer.
        """
        prompt = await self._write_prompt_to_the_wire(
            prefix_message_content_text(content, HERMES_STEER_COMMAND_PREFIX, ""),
            sender_label=sender_label,
            mode=PromptDeliveryMode.steer,
        )
        # The steer's own response says how the injection went, not how the turn goes, so
        # it is read and let go. Nothing about the running turn changes here.
        self._forget(prompt, "steer")

    async def cancel_running_turn(self) -> None:
        """Stop the turn, and do not come back until hermes says it has stopped.

        ``session/cancel`` is a notification, so sending it says nothing about when the
        turn actually ends — and the core writes the next prompt the moment this returns.
        A prompt that arrives while hermes is still finishing the turn it was told to drop
        is not the next turn: hermes holds it and answers with a note that it has been
        queued, and the reply the sender was waiting for never comes. So the stop is sent
        and then the turn's own ending is waited for, which is the only thing that makes
        "the incumbent is dead" true on the backend's account rather than on ours.
        """
        connection, session_id = self._bound_session()
        turn = self._turn
        self._require_a_live_wire()
        await self._guarded(connection.cancel(session_id=session_id))
        if turn is not None:
            await self._wait_for_the_backend_to_finish(turn)

    async def _wait_for_the_backend_to_finish(self, turn: _TurnInFlight) -> None:
        """Wait for the cancelled turn to be over at the agent, but not forever.

        Waiting for the whole ending — not just the prompt's answer — is deliberate: it is
        also what settles the turn's outstanding permission asks with hermes, so a turn
        being replaced never leaves a call of its own hanging.

        A hermes that answers its cancel takes well under a second. One that does not is
        not going to be waited on indefinitely: the conversation's ending is already
        written, so giving up here leaves the record exactly as it was and gets the next
        message moving.
        """
        finishing = [task for task in (turn.ending_reporter, turn.prompt) if task is not None]
        done, still_going = await asyncio.wait(
            finishing, timeout=CANCELLED_TURN_ENDING_TIMEOUT_SECONDS
        )
        if still_going:
            LOGGER.warning(
                "conversation %s did not hear its backend finish the turn it cancelled "
                "within %s seconds; carrying on",
                turn.token.conversation_id,
                CANCELLED_TURN_ENDING_TIMEOUT_SECONDS,
            )

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        """Give hermes the option a person chose, and wait for it to be on the wire.

        The answer is the response to a request hermes is holding open, so it is on the
        wire only once that response has been sent. Waiting for it here is what makes the
        difference between an answer that was recorded and one that was actually given.
        """
        turn = self._turn
        if turn is None:
            raise PermissionAnswerWriteFailed(ask_id)
        parked = turn.parked_asks.get(ask_id)
        if parked is None or parked.answer.done() or parked.request_id is None:
            # Nothing to answer, already answered, or an ask this child cannot tie an
            # answer back to — in which case there would be no way to know it was sent,
            # and an answer that cannot be shown to have landed has not landed.
            raise PermissionAnswerWriteFailed(ask_id)
        # Asked before anything is spent. Hermes holds one open request per ask and giving
        # it an answer uses that request up, so an answer that was never going to reach it
        # must not be the thing that uses it up: the core hands the ask back as waiting
        # when a write fails, and it has to still be an ask that can take an answer.
        self._require_a_live_wire_or_answer_failed(ask_id)

        reached_the_wire: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._permission_answer_waiters[parked.request_id] = reached_the_wire
        parked.answer.set_result(
            RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", option_id=option_id)
            )
        )
        try:
            await asyncio.wait_for(reached_the_wire, ANSWER_ON_THE_WIRE_TIMEOUT_SECONDS)
        except Exception as never_sent:
            self._permission_answer_waiters.pop(parked.request_id, None)
            # The open request this answer was the response to is used up now, whether or
            # not the answer got out, so this ask cannot take another one. It is let go
            # here and dies with its turn like any other ask nobody answered.
            turn.parked_asks.pop(ask_id, None)
            raise PermissionAnswerWriteFailed(ask_id) from never_sent
        self._permission_answer_waiters.pop(parked.request_id, None)
        turn.parked_asks.pop(ask_id, None)

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        """Hermes ACP exposes permission requests, not an agent-question request."""
        del answers
        raise UserInputAnswerWriteFailed(request_id)

    def _require_a_live_wire_or_answer_failed(self, ask_id: str) -> None:
        try:
            self._require_a_live_wire()
        except PromptWriteFailed as gone:
            raise PermissionAnswerWriteFailed(ask_id) from gone

    async def stop(self) -> None:
        """Shut the child down: stop reading, close the wire, end the process."""
        turn = self._turn
        self._turn = None
        if turn is not None:
            self._settle_parked_asks(turn)
            self._forget(turn.prompt, "prompt")
        self._give_up_on_unsent_permission_answers("this child is being shut down")
        for task in (self._standard_error_reader, self._child_watcher):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        self._standard_error_reader = None
        self._child_watcher = None
        connection = self._connection
        self._connection = None
        if connection is not None:
            # A wire that has already failed makes closing it fail too, and a child being
            # shut down has no use for the complaint.
            with suppress(Exception):
                await connection.close()
        await self._processes.aclose()

    # --- starting -----------------------------------------------------------------------

    async def _spawn(self, resolved_start: ResolvedConversationStart) -> ClientSideConnection:
        """Get the process up and speaking ACP, or say it would not start."""
        launch = self._launch
        try:
            reader, writer, process = await self._processes.enter_async_context(
                spawn_stdio_transport(
                    launch.argv[0],
                    *launch.argv[1:],
                    env=_child_environment(launch, resolved_start),
                    cwd=resolved_start.workspace_folder,
                    limit=CHILD_OUTPUT_LINE_LIMIT_BYTES,
                )
            )
        except OSError as would_not_spawn:
            raise BackendSpawnFailed(str(would_not_spawn)) from would_not_spawn
        if process.stderr is not None:
            self._standard_error_reader = asyncio.create_task(
                self._read_standard_error(process.stderr),
                name=f"planner.conversation.stderr.{resolved_start.conversation_id}",
            )
        self._child_watcher = asyncio.create_task(
            self._watch_for_the_child_ending(process),
            name=f"planner.conversation.child.{resolved_start.conversation_id}",
        )
        connection = ClientSideConnection(
            cast(Client, _AcpClientBridge(self)), writer, reader, observers=[self._observe]
        )
        self._connection = connection
        try:
            await connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name="panels", title="Panels", version=__version__),
            )
        except Exception as would_not_come_up:
            # The process is running but it is not an ACP agent this can talk to, so there
            # is no live backend to write to — which is what a spawn failure names.
            raise BackendSpawnFailed(str(would_not_come_up)) from would_not_come_up
        return connection

    async def _create_session(
        self, connection: ClientSideConnection, resolved_start: ResolvedConversationStart
    ) -> None:
        try:
            response = await connection.new_session(
                cwd=str(resolved_start.workspace_folder), mcp_servers=[]
            )
        except Exception as would_not_create:
            raise SessionLoadFailed(str(would_not_create)) from would_not_create
        self._session_id = response.session_id
        self._session_configuration_options = tuple(response.config_options or ())
        await self._sink.vendor_session_cursor_rebound(response.session_id)

    async def _load_session(
        self,
        connection: ClientSideConnection,
        resolved_start: ResolvedConversationStart,
        vendor_session_cursor: str,
    ) -> None:
        """Resume the session this conversation already has, or say it did not load.

        There is no fallback here on purpose. A resume that failed and a fresh thread in
        its place look the same to everyone downstream, and an agent that has lost the
        conversation is exactly what must never be handed back in silence.
        """
        try:
            response = await connection.load_session(
                cwd=str(resolved_start.workspace_folder),
                session_id=vendor_session_cursor,
                mcp_servers=[],
            )
        except Exception as would_not_load:
            raise SessionLoadFailed(str(would_not_load)) from would_not_load
        self._session_id = vendor_session_cursor
        self._session_configuration_options = tuple(response.config_options or ())

    async def _apply_start_values(self, resolved_start: ResolvedConversationStart) -> None:
        """Put the session on the values this conversation runs on now.

        A session that cannot be put on them is not one to write under: the text would go
        to an agent running on something nobody asked for.
        """
        try:
            await self._apply_values(resolved_start.model, resolved_start.reasoning_effort)
        except _ConfigurationNotApplied as not_applied:
            raise SessionLoadFailed(str(not_applied)) from not_applied

    # --- model and reasoning effort ------------------------------------------------------

    async def _apply_values(self, model: str | None, reasoning_effort: str | None) -> None:
        if model is not None:
            await self._apply_model(model)
        if reasoning_effort is not None:
            await self._apply_reasoning_effort(reasoning_effort)

    async def _apply_model(self, model: str) -> None:
        option = self._configuration_option(MODEL_CONFIGURATION_CATEGORY)
        if option is not None:
            await self._set_configuration_option(option.id, model)
        else:
            await self._set_legacy_session_model(model)
        self._session_model = model

    async def _apply_reasoning_effort(self, reasoning_effort: str) -> None:
        option = self._configuration_option(REASONING_EFFORT_CONFIGURATION_CATEGORY)
        if option is None:
            raise _ConfigurationNotApplied(
                "this session advertises no reasoning-effort option, so it cannot be put "
                f"on {reasoning_effort!r}"
            )
        await self._set_configuration_option(option.id, reasoning_effort)
        self._session_reasoning_effort = reasoning_effort

    async def _put_the_values_back(self, previously: tuple[str | None, str | None]) -> None:
        """Undo a change whose prompt never made it, or ask for the child to be restarted."""
        previous_model, previous_reasoning_effort = previously
        try:
            if previous_model != self._session_model:
                if previous_model is None:
                    raise _ConfigurationNotApplied(
                        "the session was on the agent's own model and there is no way to "
                        "ask for that again"
                    )
                await self._apply_model(previous_model)
            if previous_reasoning_effort != self._session_reasoning_effort:
                if previous_reasoning_effort is None:
                    raise _ConfigurationNotApplied(
                        "the session was on the agent's own reasoning effort and there is "
                        "no way to ask for that again"
                    )
                await self._apply_reasoning_effort(previous_reasoning_effort)
        except _ConfigurationNotApplied as cannot_put_back:
            raise NeedsRebind(str(cannot_put_back)) from cannot_put_back

    def _configuration_option(self, category: str) -> SessionConfigOptionSelect | None:
        """The one select option this session labels with that category, if it has one."""
        for option in self._session_configuration_options:
            if isinstance(option, SessionConfigOptionSelect) and option.category == category:
                return option
        return None

    async def _set_configuration_option(self, option_id: str, value: str) -> None:
        connection, session_id = self._bound_session_or_not_applied()
        self._require_a_live_wire_or_not_applied()
        try:
            response = await self._guarded(
                connection.set_config_option(
                    config_id=option_id, session_id=session_id, value=value
                )
            )
        except PromptWriteFailed as would_not_set:
            raise _ConfigurationNotApplied(str(would_not_set)) from would_not_set
        if response is not None and response.config_options:
            self._session_configuration_options = tuple(response.config_options)

    async def _set_legacy_session_model(self, model: str) -> None:
        """Hermes' own model change, which is not one of ACP's methods any more.

        The SDK has no wrapper for a method the protocol has retired, so the request goes
        out through the connection underneath it. That is the only way to reach a hermes
        that still answers it.
        """
        connection, session_id = self._bound_session_or_not_applied()
        self._require_a_live_wire_or_not_applied()
        raw_connection = cast(Any, connection)._conn
        try:
            await self._guarded(
                raw_connection.send_request(
                    LEGACY_SET_SESSION_MODEL_METHOD,
                    {"sessionId": session_id, "modelId": model},
                )
            )
        except PromptWriteFailed as would_not_set:
            raise _ConfigurationNotApplied(str(would_not_set)) from would_not_set

    # --- writing ------------------------------------------------------------------------

    async def _prompt_blocks(self, content: MessageContent) -> list[Any]:
        """The message as ACP content blocks.

        A picture is read off disk and sent as its bytes, because ACP's block carries the
        data itself rather than a place to find it.
        """
        blocks: list[Any] = []
        for piece in content:
            match piece:
                case MessageText():
                    blocks.append(TextContentBlock(type="text", text=piece.text))
                case MessageImage():
                    blocks.append(
                        ImageContentBlock(
                            type="image",
                            data=await self._encoded_bytes(piece.stored_file_id),
                            mime_type=piece.media_type,
                        )
                    )
        return blocks

    async def _encoded_bytes(self, stored_file_id: str) -> str:
        """The bytes of a kept file, as ACP wants them.

        A file that is not there raises, and the write becomes a refusal rather than a
        prompt with a piece missing: the person believes the agent can see their picture.
        """
        try:
            kept = await self._message_files.read(
                self._resolved_start.conversation_id, stored_file_id
            )
            return b64encode(kept).decode("ascii")
        except (MessageFileMissing, OSError) as unreadable:
            raise PromptWriteFailed(f"{stored_file_id} could not be read") from unreadable

    async def _message_piece_of(self, content: Any) -> MessagePiece | None:
        """One piece of an agent's message, out of the ACP block it arrived as.

        Words and pictures have somewhere to go. A block of any other kind is dropped,
        because a piece this system cannot say anything true about is worse in the record
        than absent — and a file an agent wants read is a markdown link in its own words,
        which arrives as words and needs nothing here.
        """
        match content:
            case TextContentBlock():
                return MessageText(text=content.text)
            case ImageContentBlock():
                return await self._kept_piece(content.data, content.mime_type)
            case _:
                return None

    async def _kept_piece(self, data: str, media_type: str) -> MessagePiece | None:
        """Keep the bytes of a picture an agent sent and name the piece that points at them.

        Bytes that will not decode, or that cannot be written down, produce no piece at
        all. The alternative is a row naming a file that is not there, which reads as a
        picture the agent sent and this system lost.
        """
        try:
            kept = await self._message_files.keep(
                self._resolved_start.conversation_id,
                b64decode(data, validate=True),
                media_type=media_type,
            )
        except (BinasciiError, OSError):
            return None
        return MessageImage(stored_file_id=kept.stored_file_id, media_type=media_type)

    async def _write_prompt_to_the_wire(
        self, content: MessageContent, *, sender_label: str, mode: PromptDeliveryMode
    ) -> asyncio.Task[Any]:
        """Start a ``session/prompt`` and return once its bytes are out, not once it answers.

        The label and the mode ride along as ACP metadata, which an agent is free to
        ignore and hermes does. Returning at the write is the whole point: the response is
        the turn's ending and waiting for it here would be waiting for the agent to finish.
        """
        connection, session_id = self._bound_session()
        self._require_a_live_wire()
        # Read off disk before the wire is touched: a picture that cannot be read is a
        # write that never happens rather than a turn started on half a message.
        prompt_blocks = await self._prompt_blocks(content)
        reached_the_wire: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._prompt_write_waiters.append(reached_the_wire)
        prompt = asyncio.create_task(
            connection.prompt(
                session_id=session_id,
                prompt=prompt_blocks,
                sender_label=sender_label,
                delivery_mode=str(mode),
            ),
            name=f"planner.conversation.prompt.{self._resolved_start.conversation_id}",
        )
        first_to_happen: set[asyncio.Future[Any]] = {reached_the_wire, prompt}
        await asyncio.wait(first_to_happen, return_when=asyncio.FIRST_COMPLETED)
        if reached_the_wire.done() and not reached_the_wire.cancelled():
            return prompt
        # The request never got out. Whatever the task is about to say about it, this write
        # did not happen, and every later one over the same wire will fail the same way.
        with suppress(ValueError):
            self._prompt_write_waiters.remove(reached_the_wire)
        self._wire_broken = True
        self._forget(prompt, "prompt")
        raise PromptWriteFailed(f"the prompt did not reach {self._describe_wire_failure(prompt)}")

    def _describe_wire_failure(self, prompt: asyncio.Task[Any]) -> str:
        if not prompt.done() or prompt.cancelled():
            return "the wire"
        failure = prompt.exception()
        return "the wire" if failure is None else f"the wire: {failure}"

    def _require_a_live_wire(self) -> None:
        """Refuse to touch a wire that has already failed.

        Once a write has failed the connection's own sender is finished, so a second
        attempt would wait for an answer that can never come instead of failing. Saying it
        plainly here is what keeps the failure at the boundary it belongs to.
        """
        if self._wire_broken:
            raise PromptWriteFailed("this child's wire has already failed")

    def _require_a_live_wire_or_not_applied(self) -> None:
        try:
            self._require_a_live_wire()
        except PromptWriteFailed as gone:
            raise _ConfigurationNotApplied(str(gone)) from gone

    async def _guarded(self, call: Any) -> Any:
        """Make a call over the wire, and remember a wire that has stopped taking them."""
        try:
            return await call
        except Exception as did_not_reach:
            self._wire_broken = True
            raise PromptWriteFailed(str(did_not_reach)) from did_not_reach

    def _bound_session(self) -> tuple[ClientSideConnection, str]:
        connection = self._connection
        session_id = self._session_id
        if connection is None or session_id is None:
            raise PromptWriteFailed("this child has no bound session")
        return connection, session_id

    def _bound_session_or_not_applied(self) -> tuple[ClientSideConnection, str]:
        try:
            return self._bound_session()
        except PromptWriteFailed as unbound:
            raise _ConfigurationNotApplied(str(unbound)) from unbound

    # --- the turn -----------------------------------------------------------------------

    def _begin_turn(self, turn_token: TurnToken, prompt: asyncio.Task[Any]) -> None:
        turn = _TurnInFlight(token=turn_token, prompt=prompt)
        self._turn = turn
        turn.ending_reporter = asyncio.create_task(
            self._report_the_ending(turn),
            name=f"planner.conversation.turn.{turn_token.conversation_id}",
        )

    async def _report_the_ending(self, turn: _TurnInFlight) -> None:
        """Wait for the prompt to answer, and turn that answer into the turn's ending."""
        ending = ConversationTurnEnding.completed
        error_summary: str | None = None
        try:
            response = await turn.prompt
        except asyncio.CancelledError:
            raise
        except Exception as failure:
            ending = ConversationTurnEnding.failed
            error_summary = str(failure)
        else:
            if str(response.stop_reason) == _CANCELLED_STOP_REASON:
                ending = ConversationTurnEnding.interrupted
            # Before the ending, because the ending is what closes the turn these counts
            # belong to. A turn that was stopped still spent what it spent.
            await self._report_what_the_turn_counted(turn, response.usage)
        await self._end_turn(turn, ending, error_summary)

    async def _report_what_the_turn_counted(self, turn: _TurnInFlight, usage: Any) -> None:
        """The turn's own token counts, which ACP carries on the answer that ends it.

        Only what hermes counted goes over. A turn it said nothing about cached tokens for
        is not a turn that read none, so an absent count stays absent. The total it also
        carries is the others added up and is left where it is, as is its count of thinking
        tokens, which this record has nowhere to put. Hermes knows nothing about money.
        """
        if not isinstance(usage, Usage):
            return
        await self._sink.token_usage_reported(
            turn.token,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_input_tokens=usage.cached_read_tokens,
            cost_usd=None,
        )

    async def _report_a_stated_cost(self, turn: _TurnInFlight, cost: Any) -> None:
        """What hermes says the session has cost, when it says it in dollars."""
        if cost is None or str(cost.currency).upper() not in _DOLLAR_CURRENCY_CODES:
            return
        await self._sink.token_usage_reported(
            turn.token,
            input_tokens=None,
            output_tokens=None,
            cached_input_tokens=None,
            cost_usd=float(cost.amount),
        )

    async def _end_turn(
        self,
        turn: _TurnInFlight,
        ending: ConversationTurnEnding,
        error_summary: str | None,
    ) -> None:
        if self._turn is not turn:
            return
        self._turn = None
        await self._complete_agent_message(turn)
        self._settle_parked_asks(turn)
        await self._sink.turn_ended(
            turn.token,
            ending=ending,
            error_summary=error_summary,
            standard_error_tail=(
                self._standard_error_tail() if ending is ConversationTurnEnding.failed else None
            ),
        )

    async def _complete_agent_message(self, turn: _TurnInFlight) -> None:
        """Hand over the message that has finished arriving, once nothing is still arriving."""
        async with self._agent_message_lock:
            await self._hand_over_the_finished_message(turn)

    async def _hand_over_the_finished_message(self, turn: _TurnInFlight) -> None:
        """The same thing, with the lock already held.

        The runs of words that arrived next to each other are joined back into one piece,
        because hermes streams a sentence in fragments and a message is not fifty pieces
        of one word. Anything that is not words stays the piece it arrived as.
        """
        if not turn.agent_message_pieces:
            return
        pieces = tuple(turn.agent_message_pieces)
        turn.agent_message_pieces.clear()
        turn.agent_message_id = None
        await self._sink.agent_message_completed(turn.token, joined_runs_of_text(pieces))

    def _settle_parked_asks(self, turn: _TurnInFlight) -> None:
        """A turn's asks die with it, and hermes is told so rather than left waiting."""
        for parked in list(turn.parked_asks.values()):
            if not parked.answer.done():
                parked.answer.set_result(
                    RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
                )
        turn.parked_asks.clear()

    def _forget(self, task: asyncio.Task[Any], what: str) -> None:
        """Read a task nobody is waiting on, so a failure is logged rather than swallowed."""

        def read(finished: asyncio.Task[Any]) -> None:
            if finished.cancelled():
                return
            failure = finished.exception()
            if failure is not None:
                LOGGER.debug(
                    "conversation %s: the %s call ended with %r",
                    self._resolved_start.conversation_id,
                    what,
                    failure,
                )

        task.add_done_callback(read)

    # --- what the agent says -------------------------------------------------------------

    async def _on_session_update(self, update: Any) -> None:
        """One piece of the agent's news, turned into what the core keeps or shows."""
        if isinstance(update, AvailableCommandsUpdate):
            # Answered above the turn, because hermes sends this when a session is
            # established — before there is a turn for it to belong to. Below the guard
            # every one of them would be dropped.
            await self._sink.composer_catalog_reported(
                _composer_catalog_entries(update.available_commands)
            )
            return
        turn = self._turn
        if turn is None:
            # The rest of the agent's news is a turn's, and news with no turn to belong to
            # names nothing the core can place, so there is nothing to do with it.
            return
        match update:
            case AgentThoughtChunk():
                # What it thought is dropped where it arrives: never stored, never
                # forwarded. That it thought is forwarded, and is all that is.
                await self._sink.model_thinking_happened(turn.token)
                return
            case AgentPlanUpdate():
                await self._sink.plan_updated(turn.token, _plan_entries(update.entries))
            case AgentMessageChunk():
                await self._on_agent_message_chunk(turn, update)
            case ToolCallStart():
                await self._sink.tool_call_started(
                    turn.token,
                    tool_call_id=update.tool_call_id,
                    title=update.title,
                    tool_kind=str(update.kind) if update.kind is not None else "other",
                    detail=_tool_call_detail(update.content),
                )
            case ToolCallProgress():
                status = _FINISHED_TOOL_CALL_STATUSES.get(str(update.status))
                if status is None:
                    # Still running, so this is progress: shown while it happens and then
                    # forgotten. Only the parts that say something are worth showing — an
                    # update that carries no readable content is a status change nobody
                    # can read, and there is nothing to show for it.
                    detail = _tool_call_detail(update.content)
                    if detail is not None:
                        await self._sink.tool_call_progress(
                            turn.token, tool_call_id=update.tool_call_id, detail=detail
                        )
                    return
                await self._sink.tool_call_finished(
                    turn.token,
                    tool_call_id=update.tool_call_id,
                    tool_call_status=status,
                    detail=_tool_call_detail(update.content),
                )
            case UsageUpdate():
                # What this update carries is how full the session's context is — used out
                # of size — which is what the turn has LEFT rather than what it has spent.
                # That is a different fact from the turn's own token counts and it has
                # nowhere to go here, so it goes nowhere: recording occupancy as this
                # turn's input tokens would put a number in the record under a name that
                # means something else, and it would disagree with the count the turn's
                # own answer gives.
                #
                # The cost is the one thing only this update knows, and it is reported when
                # hermes states it in dollars. A cost in another currency is a true number
                # this record has no field for, so it is left rather than converted.
                await self._report_a_stated_cost(turn, update.cost)
            case SessionInfoUpdate():
                if _names_a_compaction(update.field_meta):
                    await self._sink.context_compacted(turn.token)
            case _:
                return

    async def _on_agent_message_chunk(
        self, turn: _TurnInFlight, update: AgentMessageChunk
    ) -> None:
        # The lock is taken before the piece is made, because making it is what lets go
        # of the loop: a picture is read and written while this runs, and the turn's
        # ending must not slip in between that and the piece being put on the message.
        async with self._agent_message_lock:
            piece = await self._message_piece_of(update.content)
            if piece is None:
                return
            if (
                update.message_id is not None
                and turn.agent_message_id is not None
                and update.message_id != turn.agent_message_id
            ):
                # A new message id means the one before it is finished.
                await self._hand_over_the_finished_message(turn)
            if update.message_id is not None:
                turn.agent_message_id = update.message_id
            turn.agent_message_pieces.append(piece)
        # Only words stream. A picture arrives whole or not at all, so there is no
        # half-finished version of one to show and nothing is sent to the tail for it.
        if isinstance(piece, MessageText):
            await self._sink.agent_message_delta(turn.token, piece.text)

    async def _on_request_permission(
        self, tool_call: ToolCallUpdate, options: Sequence[PermissionOption]
    ) -> RequestPermissionResponse:
        """Hold the agent's ask open until a person answers it or its turn dies."""
        request_id = (
            self._incoming_permission_request_ids.popleft()
            if self._incoming_permission_request_ids
            else None
        )
        turn = self._turn
        if turn is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        self._asks_raised += 1
        ask_id = f"{tool_call.tool_call_id}:{self._asks_raised}"
        answer: asyncio.Future[RequestPermissionResponse] = (
            asyncio.get_running_loop().create_future()
        )
        turn.parked_asks[ask_id] = _ParkedPermissionAsk(answer=answer, request_id=request_id)
        await self._sink.permission_ask_raised(
            turn.token,
            BackendPermissionAsk(
                ask_id=ask_id,
                title=tool_call.title or tool_call.tool_call_id,
                detail=_tool_call_detail(tool_call.content),
                options=tuple(
                    PermissionAskOption(
                        option_id=option.option_id,
                        label=option.name,
                        option_kind=str(option.kind),
                    )
                    for option in options
                ),
            ),
        )
        return await answer

    # --- the wire itself ------------------------------------------------------------------

    def _observe(self, event: StreamEvent) -> None:
        """Watch the frames going past, for the two moments the wire itself is the answer.

        A prompt is written before it is answered, and a permission answer is sent after
        the handler that produced it returned. Neither moment is visible in the SDK's own
        call shapes, and both are exactly what this adapter has to be able to say happened.
        """
        message = event.message
        if event.direction is StreamDirection.OUTGOING:
            if message.get("method") == AGENT_METHODS["session_prompt"]:
                if self._prompt_write_waiters:
                    waiter = self._prompt_write_waiters.popleft()
                    if not waiter.done():
                        waiter.set_result(None)
                return
            if "method" not in message and "id" in message:
                answered = self._permission_answer_waiters.pop(message["id"], None)
                if answered is not None and not answered.done():
                    answered.set_result(None)
            return
        if (
            message.get("method") == CLIENT_METHODS["session_request_permission"]
            and "id" in message
        ):
            self._incoming_permission_request_ids.append(message["id"])

    async def _watch_for_the_child_ending(self, process: Any) -> None:
        """Wait for the process to go, and stop anything that is waiting on its wire.

        A turn in flight is already covered — its prompt request is rejected when the wire
        ends, and that becomes the turn's failure. An answer on its way to a permission ask
        is not: nothing else would ever wake it, and a caller waiting for an answer to land
        would wait forever instead of being told it did not.
        """
        await process.wait()
        self._wire_broken = True
        self._give_up_on_unsent_permission_answers("the backend process ended")

    def _give_up_on_unsent_permission_answers(self, why: str) -> None:
        for waiter in list(self._permission_answer_waiters.values()):
            if not waiter.done():
                waiter.set_exception(PermissionAnswerWriteFailed(why))
        self._permission_answer_waiters.clear()

    async def _read_standard_error(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                return
            self._standard_error.append(line.decode("utf-8", errors="replace"))
            while (
                sum(len(part) for part in self._standard_error)
                > STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS
                and len(self._standard_error) > 1
            ):
                self._standard_error.popleft()

    def _standard_error_tail(self) -> str | None:
        if not self._standard_error:
            return None
        return "".join(self._standard_error)[-STANDARD_ERROR_TAIL_MAXIMUM_CHARACTERS:]


class _AcpClientBridge:
    """The client side of the wire: what this adapter answers when the agent calls it.

    Only two calls are answered, because only two are offered. Panels does not lend hermes
    its file system or its terminal — hermes runs in the workspace folder with its own
    access — so those methods are not there, and saying so is what a client is supposed to
    do about a service it does not provide.
    """

    def __init__(self, child: HermesAcpBackendChild) -> None:
        self._child = child

    def on_connect(self, agent: Any) -> None:
        return None

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        del session_id, kwargs
        await self._child._on_session_update(update)

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        del session_id, kwargs
        return await self._child._on_request_permission(tool_call, options)

    async def read_text_file(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found(CLIENT_METHODS["fs_read_text_file"])

    async def write_text_file(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found(CLIENT_METHODS["fs_write_text_file"])

    async def create_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found(CLIENT_METHODS["terminal_create"])

    async def terminal_output(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found(CLIENT_METHODS["terminal_output"])

    async def release_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found(CLIENT_METHODS["terminal_release"])

    async def wait_for_terminal_exit(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found(CLIENT_METHODS["terminal_wait_for_exit"])

    async def kill_terminal(self, **kwargs: Any) -> Any:
        raise RequestError.method_not_found(CLIENT_METHODS["terminal_kill"])


class HermesAcpBackendChildFactory:
    """Makes the hermes child for one conversation. Making it does not spawn it."""

    def __init__(self, launch: AcpChildLaunch) -> None:
        self._launch = launch

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> HermesAcpBackendChild:
        return HermesAcpBackendChild(
            launch=self._launch,
            resolved_start=resolved_start,
            event_sink=event_sink,
            message_files=message_files,
        )


def _plan_entries(entries: Any) -> tuple[PlanEntry, ...]:
    """An ACP plan as this conversation's own.

    ACP words a step as its ``content`` and states it with the same three statuses this
    system uses, so the mapping is a rename. The priority ACP also carries is dropped:
    nothing here reads it, and a field nobody consumes is one more thing to keep true.
    """
    return tuple(
        PlanEntry(text=entry.content, status=PlanEntryStatus(str(entry.status)))
        for entry in entries
    )


def _composer_catalog_entries(
    commands: Sequence[AvailableCommand],
) -> tuple[ComposerCatalogEntry, ...]:
    """The commands hermes says a person may type, in this system's own words.

    ACP words the thing to type after a command's name as an input object, and the only
    kind of input it has is the unstructured one — a hint, in words, for whoever is
    writing the message. A command that takes nothing has no input at all, and so no hint.
    """
    return tuple(
        ComposerCatalogEntry(
            kind=ComposerCatalogEntryKind.command,
            display_text=f"/{command.name}",
            insertion_text=f"/{command.name} ",
            description=command.description,
            argument_hint=None if command.input is None else command.input.root.hint,
        )
        for command in commands
    )


def _names_a_compaction(metadata: dict[str, Any] | None) -> bool:
    """Whether a session info update's hermes metadata says the session was compacted.

    Every step down is checked, because ``_meta`` is a free-form blob an agent may put
    anything at all in: a shape nobody here recognises is not a compaction.
    """
    if not isinstance(metadata, dict):
        return False
    hermes_metadata = metadata.get(HERMES_METADATA_KEY)
    if not isinstance(hermes_metadata, dict):
        return False
    provenance = hermes_metadata.get(SESSION_PROVENANCE_METADATA_KEY)
    if not isinstance(provenance, dict):
        return False
    reason = provenance.get(SESSION_REPLACEMENT_REASON_KEY)
    return reason == COMPACTION_SESSION_REPLACEMENT_REASON


def _text_of(content: Any) -> str | None:
    return content.text if isinstance(content, TextContentBlock) else None


def _tool_call_detail(content: Any) -> str | None:
    """The readable part of a tool call's content, when it has one.

    A tool call carries whatever its agent put in it — text, a file edit, a terminal
    handle. Only text says anything on its own, so text is what is kept and the rest is
    left to the tool call's own title.
    """
    if not content:
        return None
    texts = [
        text
        for item in content
        if isinstance(item, ContentToolCallContent) and (text := _text_of(item.content))
    ]
    return "\n".join(texts) if texts else None
