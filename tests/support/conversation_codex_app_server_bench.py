"""The codex bench: one adapter, one scripted app-server, and what each said to the other.

This was the scaffolding of the codex adapter's own unit file. It lives here because more
than one test module needs it: the scripted child for the obligations that can only be
provoked with an app-server that answers to order, and the recording sink for the exercises
that drive the codex actually installed on this machine.

It is moved here unchanged, names and all, so what a test does with it still reads the way
it always did.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import mkdtemp
from typing import Any

from tests.unit.test_conversation_codex_scripted_app_server import scripted_app_server_launch

from planner.conversation.backends.codex_app_server.adapter import (
    CodexAppServerBackendChild,
    CodexChildLaunch,
)
from planner.conversation.backends.contracts import (
    BackendPermissionAsk,
    BackendUserInputRequest,
    TurnToken,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ConversationAccess,
    ConversationRoleMaterials,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import ConversationTurnEnding, ToolCallStatus
from planner.conversation.message_content import MessageContent, message_content_text
from planner.conversation.message_files import ConversationMessageFiles


def _message_files() -> ConversationMessageFiles:
    """A file store for this exercise, under a database path of its own.

    Every adapter is handed one, because a message can carry a file and an adapter is
    what reads it. These exercises send words, so nothing is ever written here — but the
    adapter is built the way production builds it rather than with a hole where the file
    store goes.
    """
    return ConversationMessageFiles(str(Path(mkdtemp()) / "planner.db"))



ROLE_TEXT = "You are the worker on ticket t-1."
IDENTITY_VARIABLE = ("PANELS_IDENTITY_TICKET_ID", "t-1")


def _run(exercise: Callable[[], Awaitable[None]], *, seconds: float = 30.0) -> None:
    asyncio.run(asyncio.wait_for(exercise(), seconds))


def _resolved_start(workspace: Path) -> ResolvedConversationStart:
    from planner.conversation.contracts import ConversationBackendKey

    return ResolvedConversationStart(
        conversation_id="c",
        backend_key=ConversationBackendKey.codex,
        model="gpt-5.4-mini",
        reasoning_effort="medium",
        role_materials=ConversationRoleMaterials(
            role_text=ROLE_TEXT, identity_environment_variables=(IDENTITY_VARIABLE,)
        ),
        workspace_folder=workspace,
        access=ConversationAccess.full,
    )


class _RecordingSink:
    """Everything the adapter reported, for a test driving one child directly."""

    def __init__(self) -> None:
        self.deltas: list[str] = []
        self.delta_tokens: list[TurnToken] = []
        self.agent_contents: list[MessageContent] = []
        self.agent_content_tokens: list[TurnToken] = []
        self.tool_calls_started: list[tuple[str, str, str]] = []
        self.tool_calls_progressed: list[tuple[str, str]] = []
        self.thinking_pulses: int = 0
        self.plans: list[list[tuple[str, str]]] = []
        self.token_usages: list[tuple[int | None, int | None, int | None, float | None]] = []
        self.compactions: int = 0
        self.tool_calls_finished: list[tuple[str, ToolCallStatus]] = []
        self.asks: list[BackendPermissionAsk] = []
        self.user_inputs: list[BackendUserInputRequest] = []
        self.user_input_failures: list[tuple[str, str]] = []
        self.endings: list[ConversationTurnEnding] = []
        self.ending_tokens: list[TurnToken] = []
        self.error_summaries: list[str | None] = []
        self.standard_error_tails: list[str | None] = []
        self.vendor_session_cursor: str | None = None
        self.composer_catalog_reports: list[tuple[ComposerCatalogEntry, ...]] = []
        self._turn_over = asyncio.Event()
        self._an_ask = asyncio.Event()
        self._user_input = asyncio.Event()
        self._user_input_failure = asyncio.Event()

    def expect_another_turn(self) -> None:
        self._turn_over.clear()

    async def wait_for_the_turn_to_end(self) -> None:
        await self._turn_over.wait()

    async def wait_for_an_ask(self) -> BackendPermissionAsk:
        await self._an_ask.wait()
        self._an_ask.clear()
        return self.asks[-1]

    async def wait_for_user_input(self) -> BackendUserInputRequest:
        await self._user_input.wait()
        self._user_input.clear()
        return self.user_inputs[-1]

    async def wait_for_user_input_failure(self) -> tuple[str, str]:
        await self._user_input_failure.wait()
        self._user_input_failure.clear()
        return self.user_input_failures[-1]

    async def agent_message_delta(self, turn_token: TurnToken, text_delta: str) -> None:
        self.deltas.append(text_delta)
        self.delta_tokens.append(turn_token)

    async def model_thinking_happened(self, turn_token: TurnToken) -> None:
        self.thinking_pulses += 1

    async def plan_updated(self, turn_token: TurnToken, entries: Any) -> None:
        self.plans.append([(entry.text, str(entry.status)) for entry in entries])

    async def token_usage_reported(
        self,
        turn_token: TurnToken,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        cached_input_tokens: int | None,
        cost_usd: float | None,
    ) -> None:
        self.token_usages.append((input_tokens, output_tokens, cached_input_tokens, cost_usd))

    async def context_compacted(self, turn_token: TurnToken) -> None:
        self.compactions += 1

    @property
    def agent_message_texts(self) -> list[str]:
        """The words of each finished message. The messages themselves are above."""
        return [message_content_text(content) for content in self.agent_contents]

    async def agent_message_completed(self, turn_token: TurnToken, content: MessageContent) -> None:
        self.agent_contents.append(content)
        self.agent_content_tokens.append(turn_token)

    async def tool_call_started(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        title: str,
        tool_kind: str,
        detail: str | None,
    ) -> None:
        self.tool_calls_started.append((tool_call_id, title, tool_kind))

    async def tool_call_progress(
        self, turn_token: TurnToken, *, tool_call_id: str, detail: str
    ) -> None:
        self.tool_calls_progressed.append((tool_call_id, detail))

    async def tool_call_finished(
        self,
        turn_token: TurnToken,
        *,
        tool_call_id: str,
        tool_call_status: ToolCallStatus,
        detail: str | None,
    ) -> None:
        self.tool_calls_finished.append((tool_call_id, tool_call_status))

    async def permission_ask_raised(self, turn_token: TurnToken, ask: BackendPermissionAsk) -> None:
        self.asks.append(ask)
        self._an_ask.set()

    async def user_input_requested(
        self, turn_token: TurnToken, request: BackendUserInputRequest
    ) -> None:
        self.user_inputs.append(request)
        self._user_input.set()

    async def user_input_failed(
        self, turn_token: TurnToken, *, request_id: str, detail: str
    ) -> None:
        self.user_input_failures.append((request_id, detail))
        self._user_input_failure.set()

    async def turn_ended(
        self,
        turn_token: TurnToken,
        *,
        ending: ConversationTurnEnding,
        error_summary: str | None,
        standard_error_tail: str | None,
    ) -> None:
        self.endings.append(ending)
        self.ending_tokens.append(turn_token)
        self.error_summaries.append(error_summary)
        self.standard_error_tails.append(standard_error_tail)
        self._turn_over.set()

    async def vendor_session_cursor_rebound(self, vendor_session_cursor: str) -> None:
        self.vendor_session_cursor = vendor_session_cursor

    async def composer_catalog_reported(
        self, composer_catalog: tuple[ComposerCatalogEntry, ...]
    ) -> None:
        self.composer_catalog_reports.append(composer_catalog)


@dataclass
class _ScriptedChild:
    """One adapter, one scripted app-server, and the child's own account of what it got."""

    child: CodexAppServerBackendChild
    sink: _RecordingSink
    transcript_path: Path
    workspace: Path
    # The same store the adapter was built with, so a test can keep a file and know the
    # adapter is looking in the place it was kept.
    message_files: ConversationMessageFiles

    async def start(self, *, cursor: str | None) -> None:
        await self.child.start(_resolved_start(self.workspace), vendor_session_cursor=cursor)

    async def write_prompt(
        self,
        turn_number: int,
        content: MessageContent,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        await self.child.write_prompt(
            TurnToken(conversation_id="c", turn_number=turn_number),
            content,
            sender_content=content,
            sender_label="owner",
            mode=PromptDeliveryMode.queue,
            model_change=model,
            reasoning_effort_change=reasoning_effort,
        )

    def transcript(self) -> list[dict[str, Any]]:
        if not self.transcript_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.transcript_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def all_sent(self, method: str) -> list[dict[str, Any]]:
        return [
            entry["received"]
            for entry in self.transcript()
            if "received" in entry and entry["received"].get("method") == method
        ]

    def sent(self, method: str, *, missing_is_none: bool = False) -> Any:
        sent = self.all_sent(method)
        if not sent and missing_is_none:
            return None
        assert sent, f"{method} never reached the child"
        return sent[0]

    async def wait_for_an_answer(self, *, seconds: float = 5.0) -> None:
        """Wait until an answer this adapter sent has reached the child and been written down."""
        waited = 0.0
        while not self.answers() and waited < seconds:
            await asyncio.sleep(0.05)
            waited += 0.05
        assert self.answers(), "nothing this adapter answered ever reached the child"

    def what_happened_at_the_child(self) -> list[str]:
        """The child's own order of events: what it was sent, and what it sent back.

        Ordering is the whole assertion for a race, and the only account of it that cannot
        be fooled by this side's bookkeeping is the child's.
        """
        happened: list[str] = []
        for entry in self.transcript():
            if "received" in entry and entry["received"].get("method"):
                happened.append(f"received {entry['received']['method']}")
            elif "emitted" in entry:
                happened.append(f"emitted {next(iter(entry['emitted']))}")
        return [
            step
            for step in happened
            if step.startswith("emitted") or step.split()[1] in {"turn/start", "turn/interrupt"}
        ]

    def launched(self) -> dict[str, Any]:
        launched: list[dict[str, Any]] = [
            entry["launched"] for entry in self.transcript() if "launched" in entry
        ]
        assert launched, "the child never said how it was launched"
        return launched[0]

    def answers(self) -> list[dict[str, Any]]:
        return [entry["answer"] for entry in self.transcript() if "answer" in entry]


@asynccontextmanager
async def _scripted_child(
    workspace: Path, *, script: dict[str, Any]
) -> AsyncIterator[_ScriptedChild]:
    transcript_path = workspace / "transcript.jsonl"
    script_path = workspace / "script.json"
    script_path.write_text(
        json.dumps({"transcript_path": str(transcript_path), **script}), encoding="utf-8"
    )
    argv, environment = scripted_app_server_launch(script_path)
    sink = _RecordingSink()
    message_files = ConversationMessageFiles(str(workspace / "planner.db"))
    child = CodexAppServerBackendChild(
        launch=CodexChildLaunch(argv=argv, environment_overrides=tuple(environment.items())),
        resolved_start=_resolved_start(workspace),
        event_sink=sink,
        message_files=message_files,
    )
    try:
        yield _ScriptedChild(
            child=child,
            sink=sink,
            transcript_path=transcript_path,
            workspace=workspace,
            message_files=message_files,
        )
    finally:
        await child.stop()
