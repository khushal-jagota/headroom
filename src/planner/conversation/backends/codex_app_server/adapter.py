"""Codex behind the backend seam: one child, one thread, one turn at a time.

This is the whole of what the conversation system knows about talking to codex. It owns a
``codex app-server`` subprocess and the thread the conversation resumes from. It owns none
of the conversation's rules: it never decides that a message waits, never decides that an
ask has expired, and never writes a row.

Five things about codex's app-server shape this adapter.

**A turn is started, not awaited.** ``turn/start`` answers as soon as codex has taken the
message, and the turn's ending arrives much later as a ``turn/completed`` notification. So
the write returns at acceptance — either the response or the ``turn/started`` notification,
whichever comes first — and the ending is reported when it happens. A cancel is the same
protocol read the other way round, and so has the opposite rule: ``turn/interrupt`` answers
straight away and means nothing yet, so a cancel is not finished until the turn's own
ending has arrived.

**A model or effort change is a parameter of the turn.** Codex takes both on ``turn/start``,
so a change carried by a message is simply that turn's parameters. There is nothing to set
beforehand and nothing to put back: if the turn started, the change is in force, and if it
did not, nothing stood. That is the seam's rule about changes met exactly, with no work.

**Permission asks come the other way, and the answer is the response.** Codex asks by making
a request of us and waiting; the answer a person chooses becomes the result of that request.
There is no separate call to send an answer with, which is why an ask is a request held open
until it is answered or its turn dies.

**Access is stated on every message that can state it.** Codex keeps a thread's approval
policy, reviewer and sandbox as thread state, so a value left out of one turn is the value
the last turn left behind. Panels sends all of them on the thread and again on every turn,
so what the agent is allowed to do is never inherited from something that happened earlier.

**A resume that quietly starts a new thread is refused.** ``thread/resume`` answers with the
thread it resumed; if that is not the thread that was asked for, the conversation's memory
is gone and this says so rather than handing back an agent that has forgotten everything.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import BaseModel, ValidationError

from planner import __version__
from planner.conversation.backends.codex_app_server import bindings_gen as bindings
from planner.conversation.backends.codex_app_server.client import (
    CodexAppServerClient,
    CodexAppServerError,
    CodexChildWouldNotStart,
    child_environment,
)
from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendUserInputRequest,
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

LOGGER = logging.getLogger("planner.conversation.backends.codex_app_server")

# Who Panels says it is in the handshake.
CLIENT_NAME = "panels"
CLIENT_TITLE = "Panels"

# The v2 thread and turn methods are behind codex's experimental-API flag, so the handshake
# opts in. Without it the methods this adapter is built on are not the ones it gets.
INITIALIZE_CAPABILITIES = bindings.InitializeCapabilities(experimentalApi=True)

# How full access is realized on codex: nothing is asked and nothing is fenced. The reviewer
# is stated too, because codex keeps the last one a thread was given.
FULL_ACCESS_APPROVAL_POLICY: Final = "never"
FULL_ACCESS_APPROVALS_REVIEWER: Final = "user"
FULL_ACCESS_THREAD_SANDBOX: Final = "danger-full-access"
FULL_ACCESS_TURN_SANDBOX_POLICY = bindings.DangerFullAccessSandboxPolicy(type="dangerFullAccess")

# What this adapter calls the kinds of work codex reports, in the same words the ACP
# adapter's agents use for the same things, so one pane reads both without translating.
COMMAND_EXECUTION_TOOL_KIND = "execute"
FILE_CHANGE_TOOL_KIND = "edit"
MCP_TOOL_CALL_TOOL_KIND = "other"

# The answers codex offers for an approval, in codex's own words. ``option_id`` is what goes
# back on the wire and is passed through byte for byte; the kind is how much the answer
# commits to, named the way every other backend's options name it so a surface can order
# the buttons the same way for all of them.
PERMISSION_ASK_OPTIONS: tuple[PermissionAskOption, ...] = (
    PermissionAskOption(option_id="accept", label="Approve", option_kind="allow_once"),
    PermissionAskOption(
        option_id="acceptForSession",
        label="Approve for the rest of this session",
        option_kind="allow_always",
    ),
    PermissionAskOption(option_id="decline", label="Decline", option_kind="reject_once"),
)

# What an ask still waiting when its turn dies is answered with. Codex also has ``cancel``,
# which aborts the turn it belongs to — a turn that is already over does not need aborting,
# and a decline says the same thing without reaching for a turn that may since have been
# replaced.
WITHDRAWN_ASK_DECISION = "decline"

# How much of a tool call's output is kept as its detail. The whole of a build's output is
# not a line in a conversation's record.
TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS = 4096

# How much of a command is kept as a tool call's title.
TOOL_CALL_TITLE_MAXIMUM_CHARACTERS = 200

# How long a cancel waits for codex to say the turn it interrupted has ended. Codex ends an
# interrupted turn in well under a second; this is long enough that only a codex which has
# stopped answering reaches it, and short enough that one which has does not hold up the
# message that was meant to displace the turn.
CANCEL_SETTLING_TIMEOUT_SECONDS = 15.0

# Catalogue notifications arrive in bursts while Codex rebuilds a source. Give the reader
# one short window to collect the burst before issuing one complete snapshot refresh.
CATALOG_REFRESH_COALESCE_SECONDS = 0.05
CATALOG_REQUEST_TIMEOUT_SECONDS = 60.0

# What a codex tool item's own status means to a conversation's record. Declined is a
# finish: the work was asked for and did not happen.
_FINISHED_TOOL_CALL_STATUSES: dict[str, ToolCallStatus] = {
    "completed": ToolCallStatus.completed,
    "failed": ToolCallStatus.failed,
    "declined": ToolCallStatus.failed,
}

# How a codex turn's own status is recorded. ``inProgress`` is not an ending and never
# arrives on ``turn/completed``.
_TURN_ENDINGS: dict[str, ConversationTurnEnding] = {
    "completed": ConversationTurnEnding.completed,
    "failed": ConversationTurnEnding.failed,
    "interrupted": ConversationTurnEnding.interrupted,
}

_COMPOSER_TOKEN = re.compile(r"^([^\s]+)(?:\s+(.*))?$", re.DOTALL)


@dataclass(frozen=True, slots=True)
class _CatalogInvocation:
    """The exact Codex input represented by one published composer token."""

    kind: ComposerCatalogEntryKind
    name: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class _CatalogSnapshot:
    """One complete published catalogue and the exact invocations behind it."""

    entries: tuple[ComposerCatalogEntry, ...] = ()
    invocations: tuple[tuple[str, _CatalogInvocation | None], ...] = ()

    def resolve(self, token: str) -> _CatalogInvocation | None:
        return next((invocation for key, invocation in self.invocations if key == token), None)


EMPTY_CATALOG_SNAPSHOT = _CatalogSnapshot()

CODEX_BUILT_IN_CATALOG_ENTRIES: tuple[ComposerCatalogEntry, ...] = (
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.command,
        display_text="/compact",
        insertion_text="/compact",
        description="Compact the conversation context",
    ),
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.command,
        display_text="/review",
        insertion_text="/review ",
        description="Review the working tree or follow the supplied review instructions",
        argument_hint="[review instructions]",
    ),
    ComposerCatalogEntry(
        kind=ComposerCatalogEntryKind.command,
        display_text="/goal",
        insertion_text="/goal ",
        description="Inspect, set, or clear the Codex thread goal",
        argument_hint="[get | set <objective> | clear]",
    ),
)


@dataclass(frozen=True, slots=True)
class CodexChildLaunch:
    """What to run, and what to run it with, to get a codex app-server on a pipe.

    It is a value rather than a hard-coded command so that a test can point the same adapter
    at a scripted app-server, and so the whole of what is launched is one thing to look at.
    """

    argv: tuple[str, ...]
    environment_overrides: tuple[tuple[str, str], ...] = ()


def codex_app_server_child_launch(
    *, codex_executable: Path, panels_server_url: str
) -> CodexChildLaunch:
    """The launch for the codex on this machine.

    Nothing about the login is added. Codex has one home and one account, and the login that
    account lives in is the one this process already inherits. The only thing put over it is
    where Panels is answering, which the ``panels`` CLI in the agent's shell needs and could
    not work out for itself.
    """
    return CodexChildLaunch(
        argv=(str(codex_executable), "app-server"),
        environment_overrides=(("PLAN_SERVER_URL", panels_server_url),),
    )


@dataclass(slots=True)
class _ParkedPermissionAsk:
    """One ask waiting for an answer, and the request that is held open for it."""

    request_id: Any
    method: str


@dataclass(slots=True)
class _ParkedUserInput:
    """One complete question request waiting for its complete answer map."""

    request_id: Any


@dataclass(slots=True)
class _TurnInFlight:
    """The turn this child is running, and what it has half-said so far."""

    token: TurnToken
    started: asyncio.Future[str]
    turn_id: str | None = None
    agent_message_texts: dict[str, list[str]] = field(default_factory=dict)
    parked_asks: dict[str, _ParkedPermissionAsk] = field(default_factory=dict)
    parked_user_inputs: dict[str, _ParkedUserInput] = field(default_factory=dict)
    last_error_summary: str | None = None
    # Set once codex's own account of this turn ending has arrived and been worked through.
    # It is what a cancel waits on, so the turn after it is not written into the middle of
    # codex still stopping this one.
    ended: asyncio.Event = field(default_factory=asyncio.Event)


class CodexAppServerBackendChild:
    """One codex child process, under one conversation, and its app-server wire."""

    def __init__(
        self,
        *,
        launch: CodexChildLaunch,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> None:
        self._launch = launch
        self._resolved_start = resolved_start
        self._sink = event_sink
        self._message_files = message_files
        self._client = CodexAppServerClient(
            handler=_CodexServerMessages(self), description=resolved_start.conversation_id
        )
        self._thread_id: str | None = None
        self._thread_is_ephemeral = False
        self._model: str | None = resolved_start.model
        self._reasoning_effort: str | None = resolved_start.reasoning_effort
        self._turn: _TurnInFlight | None = None
        self._asks_raised = 0
        self._catalog_snapshot = EMPTY_CATALOG_SNAPSHOT
        self._catalog_refresh_requested = False
        self._catalog_refresh_task: asyncio.Task[None] | None = None
        self._catalog_refresh_lock = asyncio.Lock()

    # --- the seam -----------------------------------------------------------------------

    async def start(
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
    ) -> None:
        """Spawn codex, shake hands, and bind the thread this conversation runs in."""
        self._resolved_start = resolved_start
        self._model = resolved_start.model
        self._reasoning_effort = resolved_start.reasoning_effort
        try:
            await self._client.start(
                argv=self._launch.argv,
                environment=child_environment(
                    overrides=self._launch.environment_overrides,
                    identity=self._identity_environment(resolved_start),
                ),
                working_directory=resolved_start.workspace_folder,
            )
        except CodexChildWouldNotStart as would_not_spawn:
            raise BackendSpawnFailed(str(would_not_spawn)) from would_not_spawn
        try:
            await self._shake_hands()
            if vendor_session_cursor is None:
                await self._start_thread(resolved_start)
            else:
                await self._resume_thread(resolved_start, vendor_session_cursor)
            await self._refresh_catalog()
        except BaseException:
            # The process is up but this child is not usable, and nobody upstream holds it
            # yet, so the only place it can be cleaned up is here.
            await self._client.stop()
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
        automatic_compaction: bool = False,
    ) -> None:
        """Start a turn with this message, on these values, and return when codex has it.

        The label and the mode have nowhere to go: codex's turn carries the text and the
        values it runs on, and has no place to say who sent it or how it was meant to
        arrive. They are dropped here rather than encoded into the text, because text put
        in front of the agent is the agent's instructions and this is not that.
        """
        del sender_label, mode, automatic_compaction
        thread_id = self._bound_thread()
        model = self._model if model_change is None else model_change
        reasoning_effort = (
            self._reasoning_effort if reasoning_effort_change is None else reasoning_effort_change
        )
        turn = _TurnInFlight(token=turn_token, started=asyncio.get_running_loop().create_future())
        self._turn = turn
        try:
            invocation = self._catalog_invocation(sender_content)
        except PromptWriteFailed:
            self._turn = None
            raise
        if invocation is not None and invocation.kind is ComposerCatalogEntryKind.command:
            command = invocation.name.split("_", 1)[0]
            if len(sender_content) != 1:
                self._turn = None
                raise PromptWriteFailed(f"/{command} cannot carry attachments")
            if model_change is not None or reasoning_effort_change is not None:
                self._turn = None
                raise PromptWriteFailed(f"/{command} cannot change the model or reasoning effort")
        native_command = (
            invocation is not None and invocation.kind is ComposerCatalogEntryKind.command
        )
        try:
            if native_command:
                assert invocation is not None
                turn.turn_id = await self._start_native_command(
                    thread_id=thread_id, invocation=invocation, turn=turn
                )
            else:
                parameters = self._turn_start_parameters(
                    thread_id=thread_id,
                    content=content,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    invocation=invocation,
                )
                turn.turn_id = await self._start_the_turn(parameters, turn)
        except PromptWriteFailed:
            # No turn started, so there is no ending to report and nothing to settle: the
            # core hears one refusal and the conversation is idle again.
            if self._turn is turn:
                self._turn = None
            raise
        # The turn is running on these values, so these are the conversation's values now.
        if not native_command:
            self._model = model
            self._reasoning_effort = reasoning_effort

    async def steer(self, content: MessageContent, *, sender_label: str) -> None:
        """Never called: codex is one of the backends the contract says cannot steer.

        Codex's app-server does have a ``turn/steer`` method. It is not used, because
        whether a backend can take text into a running turn is stated once, in the
        conversation contract, and the core refuses a steer aimed at codex before any child
        is touched. Changing that is a change to the contract, not to this adapter.
        """
        del content, sender_label
        raise PromptWriteFailed("codex does not take text into a turn that is already running")

    async def cancel_running_turn(self) -> None:
        """Stop the running turn, and return once codex says it has stopped.

        ``turn/interrupt`` answers with an empty object the moment codex has read it, which
        says nothing about the turn: the outcome arrives later as ``turn/completed`` with a
        status of interrupted. Returning at the acknowledgment would let the next message —
        a send-now's, written the instant this returns — reach codex while it is still
        winding the old turn down, and codex would take it as something to do after the
        turn it is busy ending rather than as the turn to run now.

        So this waits for codex's own account of the ending. It is bounded, because a
        backend that never says the turn ended must not wedge the conversation: on the
        bound this returns anyway, which is no worse than not having waited at all.
        """
        turn = self._turn
        if turn is None or turn.turn_id is None:
            return
        thread_id = self._bound_thread()
        parameters = bindings.TurnInterruptParams(threadId=thread_id, turnId=turn.turn_id)
        try:
            await self._client.request("turn/interrupt", _wire(parameters))
        except CodexAppServerError as did_not_reach:
            raise PromptWriteFailed(str(did_not_reach)) from did_not_reach
        try:
            await asyncio.wait_for(turn.ended.wait(), CANCEL_SETTLING_TIMEOUT_SECONDS)
        except TimeoutError:
            LOGGER.warning(
                "conversation %s: codex took the interrupt but never said the turn ended",
                self._resolved_start.conversation_id,
            )

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        """Give codex the option a person chose, as the answer to the request it is holding."""
        turn = self._turn
        parked = None if turn is None else turn.parked_asks.pop(ask_id, None)
        if parked is None or turn is None:
            raise PermissionAnswerWriteFailed(ask_id)
        try:
            answer = _approval_answer(parked.method, option_id)
        except ValidationError as not_an_answer:
            turn.parked_asks[ask_id] = parked
            raise PermissionAnswerWriteFailed(
                f"{option_id!r} is not an answer codex offers"
            ) from not_an_answer
        try:
            await self._client.respond(parked.request_id, answer)
        except CodexAppServerError as did_not_reach:
            # The answer has not landed, so the ask is still waiting for one.
            turn.parked_asks[ask_id] = parked
            raise PermissionAnswerWriteFailed(str(did_not_reach)) from did_not_reach

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        """Return every answer to the Codex request that is still held open."""
        turn = self._turn
        parked = None if turn is None else turn.parked_user_inputs.pop(request_id, None)
        if parked is None or turn is None:
            raise UserInputAnswerWriteFailed(request_id)
        try:
            answer = _user_input_answer(answers)
        except ValidationError as not_an_answer:
            turn.parked_user_inputs[request_id] = parked
            raise UserInputAnswerWriteFailed(
                f"{request_id!r} has an answer Codex does not accept"
            ) from not_an_answer
        try:
            await self._client.respond(parked.request_id, answer)
        except CodexAppServerError as did_not_reach:
            turn.parked_user_inputs[request_id] = parked
            raise UserInputAnswerWriteFailed(str(did_not_reach)) from did_not_reach

    async def stop(self) -> None:
        """Shut the child down for good."""
        refresh = self._catalog_refresh_task
        self._catalog_refresh_task = None
        if refresh is not None:
            refresh.cancel()
            with suppress(asyncio.CancelledError):
                await refresh
        turn = self._turn
        self._turn = None
        if turn is not None:
            await self._settle_parked_asks(turn)
        await self._client.stop()

    # --- starting -----------------------------------------------------------------------

    def _identity_environment(
        self, resolved_start: ResolvedConversationStart
    ) -> Sequence[tuple[str, str]]:
        role_materials = resolved_start.role_materials
        return () if role_materials is None else role_materials.identity_environment_variables

    async def _shake_hands(self) -> None:
        """Say who we are, and hear what codex is. A codex that will not is not one to use."""
        parameters = bindings.InitializeParams(
            clientInfo=bindings.ClientInfo(
                name=CLIENT_NAME, title=CLIENT_TITLE, version=__version__
            ),
            capabilities=INITIALIZE_CAPABILITIES,
        )
        try:
            await self._client.request("initialize", _wire(parameters))
            await self._client.notify("initialized")
        except CodexAppServerError as would_not_come_up:
            # The process is running but it is not an app-server this can talk to, so there
            # is no live backend to write to — which is what a spawn failure names.
            raise BackendSpawnFailed(str(would_not_come_up)) from would_not_come_up

    async def _start_thread(self, resolved_start: ResolvedConversationStart) -> None:
        parameters = bindings.ThreadStartParams(
            cwd=str(resolved_start.workspace_folder),
            model=resolved_start.model,
            approvalPolicy=_approval_policy(resolved_start.access),
            approvalsReviewer=_approvals_reviewer(resolved_start.access),
            sandbox=_thread_sandbox(resolved_start.access),
            developerInstructions=_role_text(resolved_start),
        )
        started = await self._ask_about_the_thread(
            "thread/start", parameters, bindings.ThreadStartResponse
        )
        self._thread_id = started.thread.id
        self._thread_is_ephemeral = started.thread.ephemeral
        await self._sink.vendor_session_cursor_rebound(started.thread.id)

    async def _resume_thread(
        self, resolved_start: ResolvedConversationStart, vendor_session_cursor: str
    ) -> None:
        """Pick the conversation's thread back up, or say plainly that it did not.

        There is no fallback to a fresh thread here on purpose. A resume that failed and a
        new thread in its place look the same to everyone downstream, and an agent that has
        lost the conversation is exactly what must never be handed back in silence — so the
        thread codex says it resumed is checked against the one that was asked for.
        """
        parameters = bindings.ThreadResumeParams(
            threadId=vendor_session_cursor,
            cwd=str(resolved_start.workspace_folder),
            model=resolved_start.model,
            approvalPolicy=_approval_policy(resolved_start.access),
            approvalsReviewer=_approvals_reviewer(resolved_start.access),
            sandbox=_thread_sandbox(resolved_start.access),
            developerInstructions=_role_text(resolved_start),
        )
        resumed = await self._ask_about_the_thread(
            "thread/resume", parameters, bindings.ThreadResumeResponse
        )
        if resumed.thread.id != vendor_session_cursor:
            raise SessionLoadFailed(
                f"codex was asked to resume thread {vendor_session_cursor!r} and answered "
                f"with thread {resumed.thread.id!r}, so this conversation's memory is not "
                "what came back"
            )
        self._thread_id = vendor_session_cursor
        self._thread_is_ephemeral = resumed.thread.ephemeral

    async def _ask_about_the_thread[ResponseT: BaseModel](
        self, method: str, parameters: BaseModel, response_model: type[ResponseT]
    ) -> ResponseT:
        try:
            result = await self._client.request(method, _wire(parameters))
        except CodexAppServerError as would_not_bind:
            raise SessionLoadFailed(str(would_not_bind)) from would_not_bind
        try:
            return response_model.model_validate(result)
        except ValidationError as would_not_decode:
            raise SessionLoadFailed(
                f"codex answered {method} with something this cannot read: {would_not_decode}"
            ) from would_not_decode

    # --- the composer catalogue ----------------------------------------------------------

    async def _refresh_catalog(self) -> None:
        """Publish a fresh truthful snapshot, even when one source is unavailable."""
        async with self._catalog_refresh_lock:
            snapshot = await self._read_catalog_snapshot()
            await self._sink.composer_catalog_reported(snapshot.entries)
            self._catalog_snapshot = snapshot

    def _request_catalog_refresh(self) -> None:
        """Coalesce invalidations without making the app-server reader wait for requests."""
        self._catalog_refresh_requested = True
        if self._catalog_refresh_task is not None and not self._catalog_refresh_task.done():
            return
        self._catalog_refresh_task = asyncio.create_task(
            self._run_catalog_refreshes(),
            name=f"planner.conversation.codex.catalog.{self._resolved_start.conversation_id}",
        )

    async def _run_catalog_refreshes(self) -> None:
        try:
            await asyncio.sleep(CATALOG_REFRESH_COALESCE_SECONDS)
            while self._catalog_refresh_requested:
                self._catalog_refresh_requested = False
                await self._refresh_catalog()
        finally:
            self._catalog_refresh_task = None

    async def _read_catalog_snapshot(self) -> _CatalogSnapshot:
        thread_id = self._bound_thread()
        workspace = str(self._resolved_start.workspace_folder)
        skills, installed_apps, apps, plugins = await asyncio.gather(
            self._read_skills(workspace),
            self._read_installed_apps(thread_id),
            self._read_all_apps_or_none(thread_id),
            self._read_plugins(workspace),
        )
        return _catalog_snapshot(skills, installed_apps, apps, plugins)

    async def _read_skills(self, workspace: str) -> bindings.SkillsListResponse | None:
        try:
            result = await self._catalog_request(
                "skills/list", bindings.SkillsListParams(cwds=[workspace])
            )
            skills = bindings.SkillsListResponse.model_validate(result)
        except (CodexAppServerError, ValidationError) as failed:
            self._log_catalog_source_failure("skills/list", failed)
            return None
        for entry in skills.data:
            for error in entry.errors:
                LOGGER.warning(
                    "conversation %s: skills/list could not read %s: %s",
                    self._resolved_start.conversation_id,
                    error.path,
                    error.message,
                )
        return skills

    async def _read_installed_apps(self, thread_id: str) -> bindings.AppsInstalledResponse | None:
        try:
            result = await self._catalog_request(
                "app/installed", bindings.AppsInstalledParams(threadId=thread_id)
            )
            return bindings.AppsInstalledResponse.model_validate(result)
        except (CodexAppServerError, ValidationError) as failed:
            self._log_catalog_source_failure("app/installed", failed)
            return None

    async def _read_all_apps_or_none(self, thread_id: str) -> tuple[bindings.AppInfo, ...] | None:
        try:
            return await self._read_all_apps(thread_id)
        except (CodexAppServerError, ValidationError) as failed:
            self._log_catalog_source_failure("app/list", failed)
            return None

    async def _read_plugins(self, workspace: str) -> bindings.PluginInstalledResponse | None:
        try:
            result = await self._catalog_request(
                "plugin/installed", bindings.PluginInstalledParams(cwds=[workspace])
            )
            plugins = bindings.PluginInstalledResponse.model_validate(result)
        except (CodexAppServerError, ValidationError) as failed:
            self._log_catalog_source_failure("plugin/installed", failed)
            return None
        for error in plugins.marketplaceLoadErrors or []:
            LOGGER.warning(
                "conversation %s: plugin/installed could not read %s: %s",
                self._resolved_start.conversation_id,
                error.marketplacePath,
                error.message,
            )
        return plugins

    def _log_catalog_source_failure(self, source: str, failure: Exception) -> None:
        LOGGER.warning(
            "conversation %s: Codex catalogue source %s failed: %s",
            self._resolved_start.conversation_id,
            source,
            failure,
        )

    async def _catalog_request(self, method: str, parameters: BaseModel) -> Any:
        """Read catalogue data without letting a stale source poison the prompt wire."""
        return await self._client.request(
            method,
            _wire(parameters),
            poison_wire_on_timeout=False,
            timeout_seconds=CATALOG_REQUEST_TIMEOUT_SECONDS,
        )

    async def _read_all_apps(self, thread_id: str) -> tuple[bindings.AppInfo, ...]:
        cursor: str | None = None
        all_apps: list[bindings.AppInfo] = []
        seen_cursors: set[str] = set()
        while True:
            result = await self._catalog_request(
                "app/list", bindings.AppsListParams(cursor=cursor, threadId=thread_id)
            )
            page = bindings.AppsListResponse.model_validate(result)
            all_apps.extend(page.data)
            cursor = page.nextCursor
            if cursor is None:
                return tuple(all_apps)
            if cursor in seen_cursors:
                raise CodexAppServerError("app/list repeated a pagination cursor")
            seen_cursors.add(cursor)

    def _catalog_invocation(self, content: MessageContent) -> _CatalogInvocation | None:
        if not content or not isinstance(content[0], MessageText):
            return None
        text = content[0].text
        match = _COMPOSER_TOKEN.fullmatch(text)
        if match is None:
            return None
        token = match.group(1)
        if token == "/compact":
            if text.rstrip() == "/compact":
                return _CatalogInvocation(ComposerCatalogEntryKind.command, "compact")
            raise PromptWriteFailed("/compact does not take arguments")
        if token == "/review":
            return _CatalogInvocation(
                ComposerCatalogEntryKind.command, "review", text[len("/review") :].strip()
            )
        if token == "/goal":
            return _goal_invocation(text)
        invocation = self._catalog_snapshot.resolve(token)
        if invocation is None and token.startswith(("$", "@")):
            raise PromptWriteFailed(f"{token!r} is not in the current Codex catalogue")
        return invocation

    # --- the turn -----------------------------------------------------------------------

    def _turn_input(
        self, content: MessageContent, invocation: _CatalogInvocation | None = None
    ) -> list[Any]:
        """The message as codex's own turn input.

        Codex takes a picture as the file it is, which is exactly what this system already
        has: the bytes are on disk and the piece names them, so nothing is encoded and
        nothing is copied.
        """
        given: list[Any] = []
        for piece in content:
            match piece:
                case MessageText():
                    given.append(bindings.TextUserInput(type="text", text=piece.text))
                case MessageImage():
                    given.append(
                        bindings.LocalImageUserInput(
                            type="localImage", path=str(self._kept_path(piece.stored_file_id))
                        )
                    )
                case MessageFile():
                    given.append(
                        bindings.TextUserInput(type="text", text=self._file_context(piece))
                    )
        if invocation is not None and invocation.kind is ComposerCatalogEntryKind.skill:
            given.append(
                bindings.SkillUserInput(
                    type="skill", name=invocation.name, path=invocation.path or ""
                )
            )
        elif invocation is not None and invocation.kind in {
            ComposerCatalogEntryKind.app,
            ComposerCatalogEntryKind.plugin,
        }:
            given.append(
                bindings.MentionUserInput(
                    type="mention", name=invocation.name, path=invocation.path or ""
                )
            )
        return given

    def _file_context(self, piece: MessageFile) -> str:
        path = self._kept_path(piece.stored_file_id)
        return (
            f'Attached file "{piece.file_name}" ({piece.media_type}, '
            f"{piece.byte_count} bytes) is available at {path}."
        )

    def _kept_path(self, stored_file_id: str) -> Path:
        """Where a kept file is, or a write that does not happen.

        A path handed to codex has to be a path to something. A file that is not there
        makes this a refusal rather than a turn started on a message with a hole in it.
        """
        try:
            return self._message_files.path_of(self._resolved_start.conversation_id, stored_file_id)
        except MessageFileMissing as not_there:
            raise PromptWriteFailed(f"{stored_file_id} is not there") from not_there

    def _turn_start_parameters(
        self,
        *,
        thread_id: str,
        content: MessageContent,
        model: str | None,
        reasoning_effort: str | None,
        invocation: _CatalogInvocation | None = None,
    ) -> bindings.TurnStartParams:
        access = self._resolved_start.access
        return bindings.TurnStartParams(
            threadId=thread_id,
            input=self._turn_input(content, invocation),
            model=model,
            effort=None if reasoning_effort is None else bindings.ReasoningEffort(reasoning_effort),
            approvalPolicy=_approval_policy(access),
            approvalsReviewer=_approvals_reviewer(access),
            sandboxPolicy=_turn_sandbox_policy(access),
        )

    async def _start_native_command(
        self,
        *,
        thread_id: str,
        invocation: _CatalogInvocation,
        turn: _TurnInFlight,
    ) -> str | None:
        if invocation.name == "compact":
            compact_parameters = bindings.ThreadCompactStartParams(threadId=thread_id)
            return await self._start_compaction(compact_parameters, turn)

        if invocation.name.startswith("goal_"):
            await self._run_goal_command(thread_id=thread_id, invocation=invocation, turn=turn)
            return None

        instructions = invocation.path or ""
        target: BaseModel
        if instructions:
            target = bindings.CustomReviewTarget(type="custom", instructions=instructions)
        else:
            target = bindings.UncommittedChangesReviewTarget(type="uncommittedChanges")
        review_parameters = bindings.ReviewStartParams(
            threadId=thread_id, delivery="inline", target=target
        )
        return await self._start_review(review_parameters, turn)

    async def _run_goal_command(
        self,
        *,
        thread_id: str,
        invocation: _CatalogInvocation,
        turn: _TurnInFlight,
    ) -> None:
        """Run one goal RPC and close its synthetic turn through the ordinary sink."""
        if self._thread_is_ephemeral:
            raise PromptWriteFailed("Codex goals are unavailable on an ephemeral thread")
        try:
            if invocation.name == "goal_get":
                result = await self._client.request(
                    "thread/goal/get",
                    _wire(bindings.ThreadGoalGetParams(threadId=thread_id)),
                )
                get_response = bindings.ThreadGoalGetResponse.model_validate(result)
                message = _goal_inspection_message(get_response.goal)
            elif invocation.name == "goal_set":
                result = await self._client.request(
                    "thread/goal/set",
                    _wire(
                        bindings.ThreadGoalSetParams(
                            threadId=thread_id,
                            objective=invocation.path,
                            status="active",
                        )
                    ),
                )
                set_response = bindings.ThreadGoalSetResponse.model_validate(result)
                message = f"Goal set: {set_response.goal.objective}"
            else:
                result = await self._client.request(
                    "thread/goal/clear",
                    _wire(bindings.ThreadGoalClearParams(threadId=thread_id)),
                )
                clear_response = bindings.ThreadGoalClearResponse.model_validate(result)
                message = "Goal cleared." if clear_response.cleared else "No goal was set."
        except (CodexAppServerError, ValidationError) as failed:
            raise PromptWriteFailed(str(failed)) from failed

        await self._sink.agent_message_completed(turn.token, text_message_content(message))
        if self._turn is turn:
            self._turn = None
        await self._sink.turn_ended(
            turn.token,
            ending=ConversationTurnEnding.completed,
            error_summary=None,
            standard_error_tail=None,
        )
        turn.ended.set()

    async def _start_compaction(
        self, parameters: bindings.ThreadCompactStartParams, turn: _TurnInFlight
    ) -> str | None:
        """Accept compaction on its immediate response or its standard turn notification."""
        try:
            answer = await self._client.begin_request("thread/compact/start", _wire(parameters))
        except CodexAppServerError as did_not_reach:
            raise PromptWriteFailed(str(did_not_reach)) from did_not_reach
        accepted = asyncio.create_task(
            self._client.finish_request("thread/compact/start", answer),
            name=f"planner.conversation.codex.compact.{turn.token.conversation_id}",
        )
        await asyncio.wait({accepted, turn.started}, return_when=asyncio.FIRST_COMPLETED)
        if turn.started.done() and not turn.started.cancelled():
            self._forget(accepted, "thread/compact/start")
            return turn.started.result()
        try:
            result = await accepted
            bindings.ThreadCompactStartResponse.model_validate(result)
        except (CodexAppServerError, ValidationError) as would_not_start:
            raise PromptWriteFailed(str(would_not_start)) from would_not_start
        # The documented response has no turn id. A later turn/started notification binds
        # the lifecycle to this turn token.
        return turn.turn_id

    async def _start_review(
        self, parameters: bindings.ReviewStartParams, turn: _TurnInFlight
    ) -> str:
        """Accept review/start on either acknowledgement Codex documents."""
        try:
            answer = await self._client.begin_request("review/start", _wire(parameters))
        except CodexAppServerError as did_not_reach:
            raise PromptWriteFailed(str(did_not_reach)) from did_not_reach
        accepted = asyncio.create_task(
            self._client.finish_request("review/start", answer),
            name=f"planner.conversation.codex.review.{turn.token.conversation_id}",
        )
        await asyncio.wait({accepted, turn.started}, return_when=asyncio.FIRST_COMPLETED)
        if turn.started.done() and not turn.started.cancelled():
            self._forget(accepted, "review/start")
            return turn.started.result()
        try:
            result = await accepted
            return bindings.ReviewStartResponse.model_validate(result).turn.id
        except (CodexAppServerError, ValidationError) as would_not_start:
            raise PromptWriteFailed(str(would_not_start)) from would_not_start

    async def _start_the_turn(
        self, parameters: bindings.TurnStartParams, turn: _TurnInFlight
    ) -> str:
        """Put ``turn/start`` on the wire and return the turn's id once codex has taken it.

        Codex says it has taken the turn in two ways — the response, and the ``turn/started``
        notification — and either one is enough. Waiting for both would mean waiting on
        whichever is slower for no more certainty, and waiting for the turn itself would
        mean a send that returns when the agent has finished.
        """
        try:
            answer = await self._client.begin_request("turn/start", _wire(parameters))
        except CodexAppServerError as did_not_reach:
            raise PromptWriteFailed(str(did_not_reach)) from did_not_reach
        accepted = asyncio.create_task(
            self._client.finish_request("turn/start", answer),
            name=f"planner.conversation.codex.turn.{turn.token.conversation_id}",
        )
        await asyncio.wait({accepted, turn.started}, return_when=asyncio.FIRST_COMPLETED)
        if turn.started.done() and not turn.started.cancelled():
            # The turn is running. Its response is still on its way and says nothing more
            # than that, so it is read for its failures and let go.
            self._forget(accepted, "turn/start")
            return turn.started.result()
        try:
            result = await accepted
        except CodexAppServerError as would_not_start:
            raise PromptWriteFailed(str(would_not_start)) from would_not_start
        try:
            return bindings.TurnStartResponse.model_validate(result).turn.id
        except ValidationError as would_not_decode:
            raise PromptWriteFailed(
                f"codex answered turn/start with something this cannot read: {would_not_decode}"
            ) from would_not_decode

    def _bound_thread(self) -> str:
        thread_id = self._thread_id
        if thread_id is None or not self._client.is_alive:
            raise PromptWriteFailed("this child has no live bound thread")
        return thread_id

    def _turn_this_is_about(self, turn_id: str) -> _TurnInFlight | None:
        """The running turn, when the news naming this turn id belongs to it.

        News about a turn that has already ended names a turn that is over, and there is
        nothing left for it to change.
        """
        turn = self._turn
        if turn is None or turn.turn_id != turn_id:
            return None
        return turn

    async def _end_turn(
        self,
        turn: _TurnInFlight,
        ending: ConversationTurnEnding,
        error_summary: str | None,
    ) -> None:
        if self._turn is not turn:
            return
        self._turn = None
        await self._complete_agent_messages(turn)
        await self._settle_parked_asks(turn)
        await self._sink.turn_ended(
            turn.token,
            ending=ending,
            error_summary=error_summary,
            standard_error_tail=(
                self._client.standard_error_tail()
                if ending is ConversationTurnEnding.failed
                else None
            ),
        )
        # Last, so that a cancel waiting here returns to a child with nothing of this turn
        # left to do: its asks are settled and everything it said has been reported.
        turn.ended.set()

    async def _complete_agent_messages(self, turn: _TurnInFlight) -> None:
        """Finish anything the agent was part way through saying when the turn stopped."""
        for texts in list(turn.agent_message_texts.values()):
            if texts:
                await self._sink.agent_message_completed(
                    turn.token, text_message_content("".join(texts))
                )
        turn.agent_message_texts.clear()

    async def _settle_parked_asks(self, turn: _TurnInFlight) -> None:
        """A turn's pending human interactions die with it, and Codex is told so."""
        for parked_ask in list(turn.parked_asks.values()):
            try:
                await self._client.respond(
                    parked_ask.request_id,
                    _approval_answer(parked_ask.method, WITHDRAWN_ASK_DECISION),
                )
            except (CodexAppServerError, ValidationError) as could_not_settle:
                LOGGER.debug(
                    "conversation %s: a withdrawn ask was not settled with codex: %r",
                    self._resolved_start.conversation_id,
                    could_not_settle,
                )
        turn.parked_asks.clear()
        for parked_user_input in list(turn.parked_user_inputs.values()):
            try:
                await self._client.respond(parked_user_input.request_id, _user_input_answer(()))
            except (CodexAppServerError, ValidationError) as could_not_settle:
                LOGGER.debug(
                    "conversation %s: withdrawn Codex user input was not settled: %r",
                    self._resolved_start.conversation_id,
                    could_not_settle,
                )
        turn.parked_user_inputs.clear()

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

    # --- what codex says ------------------------------------------------------------------

    async def _on_notification(self, method: str, notification: BaseModel) -> None:
        match notification:
            case bindings.TurnStartedNotification():
                self._on_turn_started(notification)
            case bindings.TurnCompletedNotification():
                await self._on_turn_completed(notification)
            case bindings.AgentMessageDeltaNotification():
                await self._on_agent_message_delta(notification)
            case bindings.ItemStartedNotification():
                await self._on_item_started(notification)
            case bindings.CommandExecutionOutputDeltaNotification():
                await self._on_tool_call_progress(
                    notification.turnId, notification.itemId, notification.delta
                )
            case bindings.McpToolCallProgressNotification():
                await self._on_tool_call_progress(
                    notification.turnId, notification.itemId, notification.message
                )
            case (
                bindings.ReasoningTextDeltaNotification()
                | bindings.ReasoningSummaryTextDeltaNotification()
            ):
                await self._on_model_thinking(notification.turnId)
            case bindings.TurnPlanUpdatedNotification():
                await self._on_plan_updated(notification)
            case bindings.ThreadTokenUsageUpdatedNotification():
                await self._on_token_usage_updated(notification)
            case bindings.ItemCompletedNotification():
                await self._on_item_completed(notification)
            case bindings.ErrorNotification():
                self._on_error(notification)
            case bindings.SkillsChangedNotification() | bindings.AppListUpdatedNotification():
                self._request_catalog_refresh()
            case _:
                LOGGER.debug("codex sent %s, which nothing here reads", method)

    def _on_turn_started(self, notification: bindings.TurnStartedNotification) -> None:
        turn = self._turn
        if turn is None or turn.turn_id is not None:
            return
        turn.turn_id = notification.turn.id
        if not turn.started.done():
            turn.started.set_result(notification.turn.id)

    async def _on_turn_completed(self, notification: bindings.TurnCompletedNotification) -> None:
        turn = self._turn_this_is_about(notification.turn.id)
        if turn is None:
            return
        ending = _TURN_ENDINGS.get(notification.turn.status)
        if ending is None:
            LOGGER.debug(
                "codex ended a turn as %s, which is not an ending", notification.turn.status
            )
            return
        error_summary: str | None = None
        if ending is ConversationTurnEnding.failed:
            error_summary = _error_summary(notification.turn.error) or turn.last_error_summary
        await self._end_turn(turn, ending, error_summary)

    async def _on_agent_message_delta(
        self, notification: bindings.AgentMessageDeltaNotification
    ) -> None:
        turn = self._turn_this_is_about(notification.turnId)
        if turn is None:
            return
        turn.agent_message_texts.setdefault(notification.itemId, []).append(notification.delta)
        await self._sink.agent_message_delta(turn.token, notification.delta)

    async def _on_tool_call_progress(self, turn_id: str, item_id: str, detail: str) -> None:
        """A tool call that has started is getting on with it.

        Codex says this two ways — a shell command's output as it is written, and an MCP
        call's own progress message — and they mean the same thing to a reader, so they
        arrive here as the same frame. Both name the item the call was started under.
        """
        turn = self._turn_this_is_about(turn_id)
        if turn is None or not detail:
            return
        await self._sink.tool_call_progress(turn.token, tool_call_id=item_id, detail=detail)

    async def _on_model_thinking(self, turn_id: str) -> None:
        """Codex streamed some private reasoning.

        Its text is not read and is not passed on — the delta is taken as nothing more
        than the sign that the model is working, which is the same thing this adapter has
        always done with reasoning, minus the silence.
        """
        turn = self._turn_this_is_about(turn_id)
        if turn is None:
            return
        await self._sink.model_thinking_happened(turn.token)

    async def _on_plan_updated(self, notification: bindings.TurnPlanUpdatedNotification) -> None:
        """The turn's plan, whole, as codex now has it.

        Codex sends the entire plan on every change, which is exactly what a plan row is,
        so it passes straight through. The explanation codex sends alongside it is prose
        about the plan rather than part of it, and nothing here has a place for it.
        """
        turn = self._turn_this_is_about(notification.turnId)
        if turn is None:
            return
        await self._sink.plan_updated(turn.token, _plan_entries(notification.plan))

    async def _on_token_usage_updated(
        self, notification: bindings.ThreadTokenUsageUpdatedNotification
    ) -> None:
        """What codex has counted so far, every time it recounts.

        Codex counts two breakdowns and both are true: ``last`` is the model request that
        has just answered, on its own, and ``total`` is everything this thread has spent.
        The total is the one passed on, because it is codex's own answer to what the work
        has cost — a turn's share of it is the step between its rows and the turn before's,
        which single requests cannot be added up into, since every request is charged for
        the whole conversation it was sent.

        Codex says nothing about money and none is worked out here from a price list this
        adapter would have to keep up to date. The context window it also sends is a fact
        about the model rather than about what the turn cost, and there is nowhere for it.

        Codex replays this after a resume, for turns that are long over. Those name a turn
        that is not running and are dropped, the same as any other late news.
        """
        turn = self._turn_this_is_about(notification.turnId)
        if turn is None:
            return
        spent_so_far = notification.tokenUsage.total
        await self._sink.token_usage_reported(
            turn.token,
            input_tokens=spent_so_far.inputTokens,
            output_tokens=spent_so_far.outputTokens,
            cached_input_tokens=spent_so_far.cachedInputTokens,
            cost_usd=None,
        )

    async def _on_item_started(self, notification: bindings.ItemStartedNotification) -> None:
        turn = self._turn_this_is_about(notification.turnId)
        if turn is None:
            return
        if isinstance(notification.item, bindings.ReasoningThreadItem):
            # What it reasoned is dropped where it arrives. That it began reasoning is the
            # earliest sign codex gives that the model is working, and it is passed on.
            await self._sink.model_thinking_happened(turn.token)
            return
        started = _tool_call_started(notification.item)
        if started is None:
            # The rest of codex's items are things a conversation's record has no row for.
            return
        title, tool_kind, detail = started
        await self._sink.tool_call_started(
            turn.token,
            tool_call_id=notification.item.id,
            title=title,
            tool_kind=tool_kind,
            detail=detail,
        )

    async def _on_item_completed(self, notification: bindings.ItemCompletedNotification) -> None:
        turn = self._turn_this_is_about(notification.turnId)
        if turn is None:
            return
        item = notification.item
        if isinstance(item, bindings.AgentMessageThreadItem):
            turn.agent_message_texts.pop(item.id, None)
            await self._sink.agent_message_completed(turn.token, text_message_content(item.text))
            return
        if isinstance(item, bindings.ContextCompactionThreadItem):
            # Codex summarised the conversation so far and dropped what it summarised. It
            # sends the compaction as an item like any other — started, then completed —
            # and the finish is the one read here, because that is where the item is codex's
            # settled account of itself and reading both would write the row twice.
            await self._sink.context_compacted(turn.token)
            return
        finished = _tool_call_finished(item)
        if finished is None:
            return
        status, detail = finished
        await self._sink.tool_call_finished(
            turn.token, tool_call_id=item.id, tool_call_status=status, detail=detail
        )

    def _on_error(self, notification: bindings.ErrorNotification) -> None:
        """Remember what went wrong, for the turn's own ending to say.

        Codex reports an error and then reports the turn, so ending the turn here would end
        it twice. A retryable error is not an ending at all.
        """
        turn = self._turn_this_is_about(notification.turnId)
        if turn is None or notification.willRetry:
            return
        turn.last_error_summary = _error_summary(notification.error)

    async def _on_server_request(self, method: str, request_id: Any, params: BaseModel) -> None:
        """Hold Codex's permission or user-input request until its turn resolves it."""
        if isinstance(params, bindings.ToolRequestUserInputParams):
            await self._on_user_input_request(request_id, params)
            return
        ask = _permission_ask_of(params)
        if ask is None:
            LOGGER.debug("codex asked %s, which nothing here reads", method)
            return
        turn_id, title, detail = ask
        turn = self._turn_this_is_about(turn_id)
        if turn is None:
            # Nothing can be answered for a turn that is over, so codex is told now rather
            # than left holding a request nobody will ever answer.
            with suppress(CodexAppServerError):
                await self._client.respond(
                    request_id, _approval_answer(method, WITHDRAWN_ASK_DECISION)
                )
            return
        self._asks_raised += 1
        ask_id = f"{turn_id}:{self._asks_raised}"
        turn.parked_asks[ask_id] = _ParkedPermissionAsk(request_id=request_id, method=method)
        await self._sink.permission_ask_raised(
            turn.token,
            BackendPermissionAsk(
                ask_id=ask_id, title=title, detail=detail, options=PERMISSION_ASK_OPTIONS
            ),
        )

    async def _on_user_input_request(
        self, wire_request_id: Any, params: bindings.ToolRequestUserInputParams
    ) -> None:
        """Raise a question request, never an execution approval."""
        turn = self._turn_this_is_about(params.turnId)
        if turn is None:
            with suppress(CodexAppServerError, ValidationError):
                await self._client.respond(wire_request_id, _user_input_answer(()))
            return

        request_id = f"{params.turnId}:{params.itemId}"
        try:
            questions = _user_input_questions(params.questions)
        except ValueError as malformed:
            await self._sink.user_input_failed(
                turn.token, request_id=request_id, detail=str(malformed)
            )
            with suppress(CodexAppServerError, ValidationError):
                await self._client.respond(wire_request_id, _user_input_answer(()))
            return

        turn.parked_user_inputs[request_id] = _ParkedUserInput(request_id=wire_request_id)
        await self._sink.user_input_requested(
            turn.token,
            BackendUserInputRequest(request_id=request_id, questions=questions),
        )

    async def _on_child_ended(self) -> None:
        """The process is gone: a turn it was running stopped, and it failed."""
        turn = self._turn
        if turn is None:
            return
        self._turn = None
        turn.parked_asks.clear()
        turn.parked_user_inputs.clear()
        await self._complete_agent_messages(turn)
        await self._sink.turn_ended(
            turn.token,
            ending=ConversationTurnEnding.failed,
            error_summary="the codex process ended while the turn was running",
            standard_error_tail=self._client.standard_error_tail(),
        )
        turn.ended.set()


class _CodexServerMessages:
    """The client's view of the adapter: what to do with what the child says."""

    def __init__(self, child: CodexAppServerBackendChild) -> None:
        self._child = child

    async def on_notification(self, method: str, notification: BaseModel) -> None:
        await self._child._on_notification(method, notification)

    async def on_server_request(self, method: str, request_id: Any, params: BaseModel) -> None:
        await self._child._on_server_request(method, request_id, params)

    async def on_child_ended(self) -> None:
        await self._child._on_child_ended()


class CodexAppServerBackendChildFactory:
    """Makes the codex child for one conversation. Making it does not spawn it."""

    def __init__(self, launch: CodexChildLaunch) -> None:
        self._launch = launch

    def __call__(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> CodexAppServerBackendChild:
        return CodexAppServerBackendChild(
            launch=self._launch,
            resolved_start=resolved_start,
            event_sink=event_sink,
            message_files=message_files,
        )


# --- access -------------------------------------------------------------------------------


def _approval_policy(access: ConversationAccess) -> Literal["never"]:
    if access is ConversationAccess.full:
        return FULL_ACCESS_APPROVAL_POLICY
    raise NotImplementedError(f"codex has no approval policy for {access}")


def _approvals_reviewer(access: ConversationAccess) -> Literal["user"]:
    if access is ConversationAccess.full:
        return FULL_ACCESS_APPROVALS_REVIEWER
    raise NotImplementedError(f"codex has no approvals reviewer for {access}")


def _thread_sandbox(access: ConversationAccess) -> Literal["danger-full-access"]:
    if access is ConversationAccess.full:
        return FULL_ACCESS_THREAD_SANDBOX
    raise NotImplementedError(f"codex has no thread sandbox for {access}")


def _turn_sandbox_policy(access: ConversationAccess) -> bindings.DangerFullAccessSandboxPolicy:
    if access is ConversationAccess.full:
        return FULL_ACCESS_TURN_SANDBOX_POLICY
    raise NotImplementedError(f"codex has no turn sandbox policy for {access}")


def _role_text(resolved_start: ResolvedConversationStart) -> str | None:
    """What the agent is told to be, put where codex takes instructions of its own.

    ``developerInstructions`` is codex's additive channel for what the client wants of the
    agent, so the role sits beside codex's own prompt rather than replacing it — which is
    what ``baseInstructions`` would do.
    """
    role_materials = resolved_start.role_materials
    return None if role_materials is None else role_materials.role_text


# --- reading codex's items -----------------------------------------------------------------


# Codex words the middle state differently from everybody else; the other two match.
_PLAN_ENTRY_STATUSES: dict[str, PlanEntryStatus] = {
    "pending": PlanEntryStatus.pending,
    "inProgress": PlanEntryStatus.in_progress,
    "completed": PlanEntryStatus.completed,
}


def _plan_entries(plan: list[bindings.TurnPlanStep]) -> tuple[PlanEntry, ...]:
    return tuple(
        PlanEntry(text=step.step, status=_PLAN_ENTRY_STATUSES[str(step.status)]) for step in plan
    )


def _tool_call_started(item: Any) -> tuple[str, str, str | None] | None:
    """The title, kind and detail of a codex item that is a piece of work, if it is one."""
    match item:
        case bindings.CommandExecutionThreadItem():
            return (
                _shortened(item.command, TOOL_CALL_TITLE_MAXIMUM_CHARACTERS),
                COMMAND_EXECUTION_TOOL_KIND,
                item.cwd,
            )
        case bindings.FileChangeThreadItem():
            return (_file_change_title(item), FILE_CHANGE_TOOL_KIND, _file_change_detail(item))
        case bindings.McpToolCallThreadItem():
            return (f"{item.server}/{item.tool}", MCP_TOOL_CALL_TOOL_KIND, None)
        case _:
            return None


def _tool_call_finished(item: Any) -> tuple[ToolCallStatus, str | None] | None:
    """How a piece of work finished, if it is a piece of work and it has finished."""
    match item:
        case bindings.CommandExecutionThreadItem():
            status = _FINISHED_TOOL_CALL_STATUSES.get(item.status)
            detail = _shortened_or_nothing(
                item.aggregatedOutput, TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS
            )
        case bindings.FileChangeThreadItem():
            status = _FINISHED_TOOL_CALL_STATUSES.get(item.status)
            detail = _file_change_detail(item)
        case bindings.McpToolCallThreadItem():
            status = _FINISHED_TOOL_CALL_STATUSES.get(item.status)
            detail = None if item.error is None else item.error.message
        case _:
            return None
    if status is None:
        # Still running: a piece of work that has not finished is not a finish.
        return None
    return status, detail


def _file_change_title(item: bindings.FileChangeThreadItem) -> str:
    paths = [change.path for change in item.changes]
    if len(paths) == 1:
        return f"Change {paths[0]}"
    return f"Change {len(paths)} files"


def _file_change_detail(item: bindings.FileChangeThreadItem) -> str | None:
    lines = [f"{change.kind.type} {change.path}" for change in item.changes]
    return _shortened("\n".join(lines), TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS)


def _permission_ask_of(params: BaseModel) -> tuple[str, str, str | None] | None:
    """The turn, title and detail of an approval codex is asking for."""
    match params:
        case bindings.CommandExecutionRequestApprovalParams():
            command = params.command or params.itemId
            return (
                params.turnId,
                f"Run {_shortened(command, TOOL_CALL_TITLE_MAXIMUM_CHARACTERS)}",
                params.reason or params.cwd,
            )
        case bindings.FileChangeRequestApprovalParams():
            return (params.turnId, "Change files", params.reason or params.grantRoot)
        case _:
            return None


def _approval_answer(method: str, decision: str) -> dict[str, Any]:
    """The answer to one of codex's approval requests, in the shape that request expects.

    The decision is passed through as codex's own word for it. Building the answer through
    the generated model is what refuses an answer codex does not offer before it reaches
    the wire rather than after.
    """
    model = (
        bindings.FileChangeRequestApprovalResponse
        if method == "item/fileChange/requestApproval"
        else bindings.CommandExecutionRequestApprovalResponse
    )
    return _wire(model.model_validate({"decision": decision}))


def _user_input_questions(
    questions: list[bindings.ToolRequestUserInputQuestion],
) -> tuple[UserInputQuestion, ...]:
    """Translate one complete Codex request, rejecting shapes the UI cannot answer."""
    if not questions:
        raise ValueError("Codex requested user input without any questions")
    translated: list[UserInputQuestion] = []
    question_ids: set[str] = set()
    for question in questions:
        question_id = question.id.strip()
        header = question.header.strip()
        prompt = question.question.strip()
        if not question_id or not header or not prompt:
            raise ValueError("Codex requested user input with a blank id, header, or question")
        if question_id in question_ids:
            raise ValueError(f"Codex repeated user-input question id {question_id!r}")
        if question.isSecret:
            raise ValueError(f"Codex question {question_id!r} asks for unsupported secret input")
        options = tuple(
            UserInputOption(label=option.label.strip(), description=option.description.strip())
            for option in (question.options or [])
        )
        if any(not option.label or not option.description for option in options):
            raise ValueError(
                f"Codex question {question_id!r} has an option without a label or description"
            )
        allow_other = bool(question.isOther) or not options
        question_ids.add(question_id)
        translated.append(
            UserInputQuestion(
                question_id=question_id,
                header=header,
                question=prompt,
                options=options,
                multi_select=False,
                allow_other=allow_other,
            )
        )
    return tuple(translated)


def _user_input_answer(answers: tuple[UserInputAnswer, ...]) -> dict[str, Any]:
    """The complete answer map in Codex's exact response shape."""
    return _wire(
        bindings.ToolRequestUserInputResponse(
            answers={
                answer.question_id: bindings.ToolRequestUserInputAnswer(
                    answers=list(answer.answers)
                )
                for answer in answers
            }
        )
    )


def _error_summary(error: bindings.TurnError | None) -> str | None:
    if error is None:
        return None
    if error.additionalDetails:
        return f"{error.message}: {error.additionalDetails}"
    return error.message


def _shortened(text: str, limit: int) -> str:
    collapsed = text.strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit] + "…"


def _shortened_or_nothing(text: str | None, limit: int) -> str | None:
    return None if text is None else _shortened(text, limit)


def _catalog_snapshot(
    skills: bindings.SkillsListResponse | None,
    installed_apps: bindings.AppsInstalledResponse | None,
    apps: tuple[bindings.AppInfo, ...] | None,
    plugins: bindings.PluginInstalledResponse | None,
) -> _CatalogSnapshot:
    """Join Codex's catalogue sources into one deterministic, executable snapshot."""
    candidates: list[tuple[str, ComposerCatalogEntry, _CatalogInvocation]] = []
    for cwd in () if skills is None else skills.data:
        for skill in cwd.skills:
            if not skill.enabled:
                continue
            token = f"${skill.name}"
            display_name = (
                skill.interface.displayName
                if skill.interface is not None and skill.interface.displayName
                else skill.name
            )
            description = (
                skill.interface.shortDescription
                if skill.interface is not None and skill.interface.shortDescription
                else skill.shortDescription or skill.description
            )
            candidates.append(
                (
                    token,
                    ComposerCatalogEntry(
                        kind=ComposerCatalogEntryKind.skill,
                        display_text=f"${display_name}",
                        insertion_text=f"{token} ",
                        description=description,
                    ),
                    _CatalogInvocation(ComposerCatalogEntryKind.skill, skill.name, skill.path),
                )
            )

    callable_apps = {
        app.id
        for app in (() if installed_apps is None else installed_apps.apps)
        if app.enabled and app.callable
    }
    for app in () if apps is None or installed_apps is None else apps:
        if app.id not in callable_apps or not app.isEnabled or not app.isAccessible:
            continue
        token = f"@{_mention_token(app.name)}"
        candidates.append(
            (
                token,
                ComposerCatalogEntry(
                    kind=ComposerCatalogEntryKind.app,
                    display_text=f"@{app.name}",
                    insertion_text=f"{token} ",
                    description=app.description or f"Use {app.name}",
                ),
                _CatalogInvocation(ComposerCatalogEntryKind.app, app.name, f"app://{app.id}"),
            )
        )

    for marketplace in () if plugins is None else plugins.marketplaces:
        for plugin in marketplace.plugins:
            if (
                not plugin.installed
                or not plugin.enabled
                or plugin.availability == "DISABLED_BY_ADMIN"
                or plugin.disabledReason is not None
            ):
                continue
            display_name = (
                plugin.interface.displayName
                if plugin.interface is not None and plugin.interface.displayName
                else plugin.name
            )
            description = (
                plugin.interface.shortDescription
                if plugin.interface is not None and plugin.interface.shortDescription
                else f"Use {display_name}"
            )
            # A marketplace is part of a plugin's protocol identity. Keep it in the token
            # too, so two installed marketplaces can never turn the same text into a
            # different plugin because catalogue ordering changed.
            token = f"@{_mention_token(plugin.name)}@{_mention_token(marketplace.name)}"
            candidates.append(
                (
                    token,
                    ComposerCatalogEntry(
                        kind=ComposerCatalogEntryKind.plugin,
                        display_text=f"@{display_name}",
                        insertion_text=f"{token} ",
                        description=description,
                    ),
                    _CatalogInvocation(
                        ComposerCatalogEntryKind.plugin,
                        display_name,
                        f"plugin://{plugin.name}@{marketplace.name}",
                    ),
                )
            )

    deduplicated: dict[
        tuple[str, str, str], tuple[str, ComposerCatalogEntry, _CatalogInvocation]
    ] = {}
    for candidate in candidates:
        invocation = candidate[2]
        identity = (str(invocation.kind), invocation.name, invocation.path or "")
        deduplicated.setdefault(identity, candidate)

    by_token: dict[str, list[tuple[str, ComposerCatalogEntry, _CatalogInvocation]]] = {}
    for candidate in deduplicated.values():
        by_token.setdefault(candidate[0], []).append(candidate)

    executable: list[tuple[str, ComposerCatalogEntry, _CatalogInvocation]] = []
    allocated_tokens = set(by_token)
    for token in sorted(by_token, key=lambda value: (value.casefold(), value)):
        colliding = by_token[token]
        if len(colliding) == 1:
            executable.append(colliding[0])
            continue
        colliding.sort(
            key=lambda candidate: (
                str(candidate[2].kind),
                candidate[2].name,
                candidate[2].path or "",
            )
        )
        for _, entry, invocation in colliding:
            alias = _collision_alias(token, invocation, allocated_tokens)
            allocated_tokens.add(alias)
            executable.append(
                (
                    alias,
                    replace(entry, display_text=alias, insertion_text=f"{alias} "),
                    invocation,
                )
            )
    executable.sort(key=lambda candidate: (str(candidate[1].kind), candidate[0].casefold()))

    invocations: list[tuple[str, _CatalogInvocation | None]] = [
        ("/compact", _CatalogInvocation(ComposerCatalogEntryKind.command, "compact")),
        ("/review", _CatalogInvocation(ComposerCatalogEntryKind.command, "review")),
        ("/goal", _CatalogInvocation(ComposerCatalogEntryKind.command, "goal_get")),
    ]
    invocations.extend((token, invocation) for token, _, invocation in executable)
    return _CatalogSnapshot(
        entries=CODEX_BUILT_IN_CATALOG_ENTRIES + tuple(entry for _, entry, _ in executable),
        invocations=tuple(invocations),
    )


def _collision_alias(
    canonical_token: str,
    invocation: _CatalogInvocation,
    allocated_tokens: set[str],
) -> str:
    """Allocate a deterministic alias outside every canonical and prior alias token."""
    identity = f"{invocation.kind}\0{invocation.name}\0{invocation.path or ''}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    for length in range(8, len(digest) + 1):
        alias = f"{canonical_token}~{digest[:length]}"
        if alias not in allocated_tokens:
            return alias
    serial = 2
    while f"{canonical_token}~{digest}-{serial}" in allocated_tokens:
        serial += 1
    return f"{canonical_token}~{digest}-{serial}"


def _goal_invocation(text: str) -> _CatalogInvocation:
    """Parse the complete native goal command, or refuse a malformed known form."""
    command = text.rstrip()
    if command in {"/goal", "/goal get"}:
        return _CatalogInvocation(ComposerCatalogEntryKind.command, "goal_get")
    if command == "/goal clear":
        return _CatalogInvocation(ComposerCatalogEntryKind.command, "goal_clear")
    set_prefix = "/goal set"
    if (
        command.startswith(set_prefix)
        and len(command) > len(set_prefix)
        and command[len(set_prefix)].isspace()
    ):
        objective = command[len(set_prefix) :].strip()
        if objective:
            return _CatalogInvocation(ComposerCatalogEntryKind.command, "goal_set", objective)
    raise PromptWriteFailed("/goal accepts no argument, 'get', 'set <objective>', or 'clear'")


def _goal_inspection_message(goal: bindings.ThreadGoal | None) -> str:
    if goal is None:
        return "No goal is set."
    budget = "" if goal.tokenBudget is None else f"/{goal.tokenBudget}"
    return (
        f"Goal: {goal.objective} ({goal.status}, "
        f"{goal.tokensUsed}{budget} tokens, {goal.timeUsedSeconds}s)"
    )


def _mention_token(name: str) -> str:
    """Codex's stable lower-case token spelling for a visible mention name."""
    token = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    return token or "item"


def _wire(parameters: BaseModel) -> dict[str, Any]:
    """A generated model as the JSON codex is sent: what was asked for, and nothing else.

    Absent fields are left out rather than sent as nulls, because codex tells absent from
    null on the fields that override thread state and a null is not "leave this alone".
    Fields nobody set are left out too: a schema default this side did not choose is
    codex's own default, and codex applies it whether or not it is echoed back at it.
    """
    dumped: dict[str, Any] = parameters.model_dump(
        mode="json", exclude_none=True, exclude_unset=True, by_alias=True
    )
    return dumped
