"""The conversation system over HTTP, driven the way a browser drives it.

The subject is the real system on a real database, behind the real router. What stands in
for a vendor is one fake backend adapter, so a test asking "did this text reach the agent"
is answered by the backend side's own account rather than by the system's report of itself.

The endless routes — the tail — are driven straight as an ASGI application, because a
response that never ends cannot be collected first and handed back afterwards.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
import zlib
from collections.abc import Callable, Coroutine, Iterator, MutableMapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import planner.conversation.api as conversation_api
import planner.conversation.contracts as conversation_contracts
import planner.conversation.system as conversation_system
from planner.conversation.api import (
    COMMITTED_EVENT_STREAM_NAME,
    LIVE_FRAME_STREAM_NAME,
    PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS,
    ConversationRuntime,
    SentFilePiece,
    conversation_message_content,
    router,
)
from planner.conversation.backend_state import BackendStateStore
from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
    BackendUsageService,
    BackendUsageWindow,
    BackendUsageWindowKind,
)
from planner.conversation.backends.claude_model_catalog import (
    ClaudeModel,
    ClaudeModelCatalog,
)
from planner.conversation.backends.codex_app_server.model_catalog import (
    CodexModelCatalog,
    CodexModelCatalogUnavailable,
)
from planner.conversation.backends.contracts import (
    BackendEventSink,
    BackendPermissionAsk,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerOutcome,
    BackendSteerUncertain,
    BackendUserInputRequest,
    NeedsRebind,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import (
    ComposerCatalogEntry,
    ComposerCatalogEntryKind,
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AgentMessageEventPayload,
    ConversationTurnEnding,
    PermissionAskOption,
    ToolCallFinishedEventPayload,
    ToolCallStartedEventPayload,
    ToolCallStatus,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
)
from planner.conversation.image_validation import MAX_CONVERSATION_MESSAGE_IMAGE_BYTES
from planner.conversation.live_tail import MAXIMUM_HELD_TAIL_ITEMS, ConversationLiveTail
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    MessageText,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.snapshot import (
    BackendModel,
    BackendSnapshot,
    BackendSnapshotService,
    CommandOutcome,
)
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core import sse
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.response_compression import (
    CompressExceptEventStreams,
    event_stream_route_patterns,
)
from planner.core.server import create_app
from planner.files.logic.paths import conversation_files_root
from planner.tickets import data as tickets_data

VENDOR_SESSION_CURSOR = "vendor-session-1"
HEARTBEAT_MILLISECONDS = 40
CONVERSATION_PREFIX = "/api/conversation"


# --- the backend side ---------------------------------------------------------------------


@dataclass
class _FakeBackend:
    """One conversation's backend side, kept across every child spawned for it."""

    conversation_id: str
    written_contents: list[MessageContent] = field(default_factory=list)

    @property
    def written_texts(self) -> list[str]:
        """The words of each message written. The messages themselves are above."""
        return [message_content_text(content) for content in self.written_contents]
    steered_contents: list[MessageContent] = field(default_factory=list)
    steer_outcome: BackendSteerOutcome = field(default_factory=BackendSteerAccepted)
    permission_answers: dict[str, str] = field(default_factory=dict)
    user_input_answers: dict[str, tuple[UserInputAnswer, ...]] = field(default_factory=dict)
    cancellations: int = 0
    live_turn_token: TurnToken | None = None
    sink: BackendEventSink | None = None
    asks_raised: int = 0
    spawn_fails: bool = False
    write_fails: bool = False
    needs_failed_child_recovery_once: bool = False
    session_load_fails: bool = False


class _FakeBackendChild:
    def __init__(self, backend: _FakeBackend, event_sink: BackendEventSink) -> None:
        self._backend = backend
        self._sink = event_sink

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        del resolved_start
        if self._backend.session_load_fails:
            raise SessionLoadFailed(self._backend.conversation_id)
        self._backend.sink = self._sink
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
        del (
            sender_content,
            sender_label,
            mode,
            model_change,
            reasoning_effort_change,
            automatic_compaction,
        )
        if self._backend.needs_failed_child_recovery_once:
            self._backend.needs_failed_child_recovery_once = False
            raise NeedsRebind(
                self._backend.conversation_id, failed_child_recovery=True
            )
        if self._backend.write_fails:
            raise PromptWriteFailed(self._backend.conversation_id)
        self._backend.written_contents.append(content)
        self._backend.live_turn_token = turn_token

    async def steer(
        self, turn_token: TurnToken, content: MessageContent, *, sender_label: str
    ) -> BackendSteerOutcome:
        del turn_token
        del sender_label
        self._backend.steered_contents.append(content)
        return self._backend.steer_outcome

    async def cancel_running_turn(self) -> None:
        self._backend.cancellations += 1
        self._backend.live_turn_token = None

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        self._backend.permission_answers[ask_id] = option_id

    async def answer_user_input(
        self, request_id: str, answers: tuple[UserInputAnswer, ...]
    ) -> None:
        self._backend.user_input_answers[request_id] = answers

    async def stop(self) -> None:
        self._backend.live_turn_token = None


# --- the machine, without touching the machine --------------------------------------------


@dataclass
class _FakeMachine:
    """Everything the snapshot is allowed to touch, answered from a script."""

    executables: dict[str, str] = field(default_factory=dict)
    outcomes: dict[tuple[str, ...], CommandOutcome] = field(default_factory=dict)
    run_commands: list[tuple[str, ...]] = field(default_factory=list)
    answers_any_other_command: CommandOutcome | None = None

    def executable_path(self, executable_name: str) -> str | None:
        return self.executables.get(executable_name)

    def configured_executable_path(self, executable_name: str) -> str | None:
        return self.executables.get(executable_name) if executable_name == "hermes" else None

    def real_path(self, path: str) -> str:
        return path

    def user_local_npm_prefix(self) -> str:
        return str(Path.home() / ".local")

    def prefix_is_owned_and_writable(self, prefix: str) -> bool:
        del prefix
        return False

    async def run(
        self,
        argv: Any,
        *,
        timeout_seconds: float,
        environment_overrides: Any = None,
    ) -> CommandOutcome:
        del timeout_seconds, environment_overrides
        command = tuple(argv)
        self.run_commands.append(command)
        return self.outcomes.get(
            command,
            self.answers_any_other_command
            or CommandOutcome(exit_code=-1, standard_output="", standard_error="no such"),
        )

    async def latest_released_version(
        self, package_name: str, *, timeout_seconds: float
    ) -> str | None:
        del package_name, timeout_seconds
        return None


async def _claude_from_the_handshake(claude_executable: str) -> ClaudeModelCatalog:
    del claude_executable
    models = (
        ClaudeModel(
            model_id="opus[1m]",
            display_name="Opus 5 (1M)",
            resolved_model_id="claude-opus-5[1m]",
            reasoning_effort_options=("low", "medium", "high", "xhigh", "max"),
        ),
        ClaudeModel(
            model_id="sonnet",
            display_name="Sonnet 5",
            resolved_model_id="claude-sonnet-5",
            reasoning_effort_options=("low", "medium", "high", "xhigh", "max"),
        ),
    )
    return ClaudeModelCatalog(
        models=models, reasoning_effort_options=("low", "medium", "high", "xhigh", "max")
    )


async def _no_codex_to_ask(codex_executable: str) -> CodexModelCatalog:
    """No test here spawns a codex app-server to ask it what it runs."""
    del codex_executable
    raise CodexModelCatalogUnavailable("no codex in a unit test")


# --- the subject ---------------------------------------------------------------------------


class _Harness:
    """The real router over the real system, with a fake backend and a fake machine."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.store = ConversationStore(str(db_path))
        self.live_tail = ConversationLiveTail()
        self.backends: dict[str, _FakeBackend] = {}
        self.machine = _FakeMachine()
        self.message_files = ConversationMessageFiles(str(db_path))
        self.system = SqliteProcessConversationSystem(
            store=self.store,
            message_files=self.message_files,
            backend_child_factories={
                backend_key: self._make_child for backend_key in ConversationBackendKey
            },
            live_tail=self.live_tail,
        )
        self.runtime = ConversationRuntime(
            store=self.store,
            system=self.system,
            live_tail=self.live_tail,
            message_files=self.message_files,
            database_path=str(db_path),
            backend_snapshots=BackendSnapshotService(
                self.machine,
                codex_model_catalog_probe=_no_codex_to_ask,
                claude_model_catalog_probe=_claude_from_the_handshake,
            ),
            backend_usage=BackendUsageService({}),
            sse_heartbeat_ms=HEARTBEAT_MILLISECONDS,
        )
        self.app = FastAPI()
        self.app.include_router(router, prefix=CONVERSATION_PREFIX)
        self.app.state.conversation = self.runtime

    def _make_child(
        self,
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _FakeBackendChild:
        backend = self.backend(resolved_start.conversation_id)
        if backend.spawn_fails:
            raise BackendSpawnFailed(resolved_start.conversation_id)
        return _FakeBackendChild(backend, event_sink)

    def backend(self, conversation_id: str) -> _FakeBackend:
        return self.backends.setdefault(conversation_id, _FakeBackend(conversation_id))

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://conversation"
        )

    async def settle(self) -> None:
        await self.system.wait_until_quiescent()

    async def complete_turn(self, conversation_id: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        backend.live_turn_token = None
        await backend.sink.turn_ended(
            token,
            ending=ConversationTurnEnding.completed,
            error_summary=None,
            standard_error_tail=None,
        )
        await self.settle()

    async def fail_turn(self, conversation_id: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        backend.live_turn_token = None
        await backend.sink.turn_ended(
            token,
            ending=ConversationTurnEnding.failed,
            error_summary="the terminal stream failed",
            standard_error_tail=None,
        )
        await self.settle()

    async def raise_permission_ask(self, conversation_id: str) -> str:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        backend.asks_raised += 1
        ask_id = f"ask-{backend.asks_raised}"
        await backend.sink.permission_ask_raised(
            token,
            BackendPermissionAsk(
                ask_id=ask_id,
                title="Run a command?",
                detail="ls -la",
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

    async def stream_agent_text(self, conversation_id: str, text_delta: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.agent_message_delta(token, text_delta)
        await self.settle()

    async def stream_tool_output(
        self, conversation_id: str, tool_call_id: str, detail: str
    ) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.tool_call_progress(
            token, tool_call_id=tool_call_id, detail=detail
        )
        await self.settle()

    async def model_is_thinking(self, conversation_id: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.model_thinking_happened(token)
        await self.settle()

    async def start_tool_call(self, conversation_id: str, tool_call_id: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.tool_call_started(
            token,
            tool_call_id=tool_call_id,
            title="Run a command",
            tool_kind="execute",
            detail=None,
        )
        await self.settle()

    async def finish_tool_call(self, conversation_id: str, tool_call_id: str) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.tool_call_finished(
            token,
            tool_call_id=tool_call_id,
            tool_call_status=ToolCallStatus.completed,
            detail=None,
        )
        await self.settle()


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[_Harness]:
    db_path = tmp_path / "conversation-api.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    built = _Harness(db_path)
    yield built
    asyncio.run(built.runtime.shutdown())


def _run(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(exercise(), 20.0))


def _associate_ticket_conversations(
    harness: _Harness, *, active: str, past: str
) -> None:
    conn = connect(str(harness.db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            title="Conversation boundary",
            worker_type="coding",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (past, ticket.id),
        )
        conn.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (active, ticket.id),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ? WHERE id = ?",
            (active, ticket.id),
        )
    finally:
        conn.close()


async def _start(
    client: httpx.AsyncClient,
    conversation_id: str = "c",
    *,
    backend_key: str = "hermes",
    **rest: Any,
) -> httpx.Response:
    return await client.post(
        "/api/conversation/conversations",
        json={
            "conversation_id": conversation_id,
            "model": "a-model",
            "backend_key": backend_key,
            "workspace_folder": "/tmp/workspace",
            **rest,
        },
    )


# --- driving a response that never ends ----------------------------------------------------


class _EventStreamDrive:
    """One open SSE response, read frame by frame while the test carries on."""

    def __init__(
        self,
        app: FastAPI,
        path: str,
        query: str,
        headers: list[tuple[bytes, bytes]] | None = None,
        entry: Any = None,
    ) -> None:
        self._app = app
        # What is actually called. The scope still names the FastAPI application, so
        # middleware under test can wrap the route without hiding `request.app`.
        self._entry = entry or app
        self._path = path
        self._query = query
        self._headers = headers or []
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = ""
        self._task: asyncio.Task[None] | None = None
        self.started: dict[str, Any] = {}

    async def __aenter__(self) -> _EventStreamDrive:
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": self._path,
            "raw_path": self._path.encode(),
            "root_path": "",
            "query_string": self._query.encode(),
            "headers": self._headers,
            "client": ("127.0.0.1", 51234),
            "server": ("127.0.0.1", 8767),
            "app": self._app,
        }

        async def receive() -> dict[str, Any]:
            # The browser is still there: nothing is ever sent up this stream.
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                self.started.update(message)
            elif message["type"] == "http.response.body":
                await self._chunks.put(bytes(message.get("body", b"")))

        self._task = asyncio.create_task(self._entry(scope, receive, send))
        return self

    async def __aexit__(self, *exception: object) -> None:
        task = self._task
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def wait_until_watching(self, live_tail: ConversationLiveTail) -> None:
        """Wait for the route to have registered its watch.

        A frame published before anyone is watching is gone, which is what an ephemeral
        frame is — so a test about live frames has to be watching before it makes one.
        """
        for _ in range(2_000):
            if live_tail.open_subscription_count() > 0:
                return
            await asyncio.sleep(0.001)
        raise AssertionError("the tail never opened")

    async def next_frame(self, timeout: float = 5.0) -> str:
        """The next whole SSE frame, heartbeats and all."""
        while "\n\n" not in self._buffer:
            chunk = await asyncio.wait_for(self._chunks.get(), timeout=timeout)
            self._buffer += chunk.decode()
        frame, _, rest = self._buffer.partition("\n\n")
        self._buffer = rest
        return frame + "\n\n"

    async def next_named_frame(self, timeout: float = 5.0) -> tuple[str, dict[str, Any]]:
        """The next frame that is not a heartbeat, as its name and its payload."""
        while True:
            frame = await self.next_frame(timeout)
            if frame.startswith(":"):
                continue
            name_line, data_line = frame.strip().split("\n", 1)
            return name_line.removeprefix("event: "), json.loads(
                data_line.removeprefix("data: ")
            )


# --- starting a conversation ----------------------------------------------------------------


def test_every_conversation_mutation_rejects_a_tickets_past_conversation(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            assert (await _start(client, "past")).status_code == 201
            assert (await _start(client, "active")).status_code == 201
            _associate_ticket_conversations(harness, active="active", past="past")
            past_files = conversation_files_root(harness.db_path) / "past"
            assert not past_files.exists()

            requests = (
                (
                    "POST",
                    "/api/conversation/conversations/past/send",
                    {
                        "content": [{"piece": "text", "text": "do not send"}],
                        "sender_label": "owner",
                    },
                ),
                ("POST", "/api/conversation/conversations/past/interrupt", None),
                ("POST", "/api/conversation/conversations/past/kill", None),
                (
                    "DELETE",
                    "/api/conversation/conversations/past/held-prompts/held-1",
                    None,
                ),
                (
                    "POST",
                    "/api/conversation/conversations/past/held-prompts/held-1/promote",
                    {"mode": "send_now"},
                ),
                (
                    "POST",
                    "/api/conversation/conversations/past/permission-answers",
                    {"ask_id": "ask-1", "option_id": "allow"},
                ),
                (
                    "POST",
                    "/api/conversation/conversations/past/user-input-answers",
                    {"request_id": "input-1", "answers": {}},
                ),
            )
            for method, path, body in requests:
                response = await client.request(method, path, json=body)
                assert response.status_code == 409, (method, path, response.text)
                assert response.json()["detail"] == (
                    "the conversation is not the Ticket's active conversation"
                )
            assert not past_files.exists()

            active_send = await client.post(
                "/api/conversation/conversations/active/send",
                json={
                    "content": [{"piece": "text", "text": "active work"}],
                    "sender_label": "owner",
                },
            )
            assert active_send.status_code == 200

            assert (await _start(client, "unassociated")).status_code == 201
            unassociated_send = await client.post(
                "/api/conversation/conversations/unassociated/send",
                json={
                    "content": [{"piece": "text", "text": "development work"}],
                    "sender_label": "owner",
                },
            )
            assert unassociated_send.status_code == 200

    _run(exercise)


@pytest.mark.parametrize("backend_key", tuple(ConversationBackendKey))
def test_starting_a_conversation_answers_with_what_it_resolved_to(
    harness: _Harness,
    monkeypatch: pytest.MonkeyPatch,
    backend_key: ConversationBackendKey,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            monkeypatch.setattr(
                conversation_contracts,
                "BACKEND_KEYS_SUPPORTING_STEER",
                frozenset(ConversationBackendKey),
            )
            response = await _start(
                client,
                "c",
                backend_key=str(backend_key),
                model="a-model",
                reasoning_effort="high",
            )

            assert response.status_code == 201
            view = response.json()
            assert view["conversation_id"] == "c"
            assert view["backend_key"] == str(backend_key)
            assert view["model"] == "a-model"
            assert view["reasoning_effort"] == "high"
            assert view["workspace_folder"] == "/tmp/workspace"
            # The floor default, applied because the request said nothing about access.
            assert view["access"] == "full"
            assert view["is_running"] is False
            assert view["supports_steer"] is True
            assert view["latest_sequence"] == 0
            assert view["held_prompts"] == []
            assert view["pending_permission_ask"] is None
            # Nothing has reported a menu, so there is none to offer.
            assert view["composer_catalog"] == []

    _run(exercise)


def test_the_view_reports_a_controlled_capability_off(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(
            conversation_contracts,
            "BACKEND_KEYS_SUPPORTING_STEER",
            frozenset(),
        )
        async with harness.client() as client:
            response = await _start(client, "c")
            assert response.json()["supports_steer"] is False

    _run(exercise)


def test_the_view_carries_the_typed_composer_catalog(
    harness: _Harness,
) -> None:
    """Each command as its name, what it does, and what to type after it, in order."""

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "first"}],
                    "sender_label": "owner",
                    "mode": "queue",
                },
            )
            backend = harness.backend("c")
            assert backend.sink is not None
            await backend.sink.composer_catalog_reported(
                (
                    ComposerCatalogEntry(
                        kind=ComposerCatalogEntryKind.command,
                        display_text="/review",
                        insertion_text="/review ",
                        description="Review the diff",
                        argument_hint="[path]",
                    ),
                    ComposerCatalogEntry(
                        kind=ComposerCatalogEntryKind.plugin,
                        display_text="@compact",
                        insertion_text="@compact exact ",
                        description="Summarise the conversation so far",
                    ),
                )
            )
            await harness.settle()

            view = (await client.get("/api/conversation/conversations/c")).json()
            assert view["composer_catalog"] == [
                {
                    "kind": "command",
                    "display_text": "/review",
                    "insertion_text": "/review ",
                    "description": "Review the diff",
                    "argument_hint": "[path]",
                },
                {
                    "kind": "plugin",
                    "display_text": "@compact",
                    "insertion_text": "@compact exact ",
                    "description": "Summarise the conversation so far",
                    "argument_hint": None,
                },
            ]

    _run(exercise)


# --- sending -----------------------------------------------------------------------------


def test_every_fate_a_send_can_have_comes_back_tagged(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_system, "backend_supports_steer", lambda _key: True)
        monkeypatch.setattr(conversation_api, "backend_supports_steer", lambda _key: True)
        async with harness.client() as client:
            await _start(client, "c")
            assert (
                await client.get("/api/conversation/conversations/c")
            ).json()["supports_steer"] is True

            started = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "first"}],
                    "sender_label": "owner",
                    "mode": "queue",
                },
            )
            assert started.status_code == 200
            assert started.json() == {"fate": "started"}

            queued = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held"}],
                    "sender_label": "owner",
                    "mode": "queue",
                },
            )
            assert queued.json() == {"fate": "queued", "queue_position": 1}

            injected = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "also this"}],
                    "sender_label": "owner",
                    "mode": "steer",
                },
            )
            assert injected.json() == {"fate": "injected"}
            assert harness.backend("c").steered_contents == [text_message_content("also this")]

            harness.backend("c").steer_outcome = BackendSteerUncertain()
            uncertain = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "unclear"}],
                    "sender_label": "owner",
                    "mode": "steer",
                },
            )
            assert uncertain.json() == {"fate": "uncertain"}

            refused = await client.post(
                "/api/conversation/conversations/unknown/send",
                json={
                    "content": [{"piece": "text", "text": "nowhere"}],
                    "sender_label": "owner",
                    "mode": "queue",
                },
            )
            assert refused.status_code == 200
            assert refused.json() == {
                "fate": "refused",
                "refusal_reason": "no_such_conversation",
            }

    _run(exercise)


def test_a_failed_claude_recovery_refusal_remains_after_an_api_reread(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c", backend_key="claude")
            first = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "first"}],
                    "sender_label": "owner",
                },
            )
            assert first.json() == {"fate": "started"}
            await harness.fail_turn("c")
            backend = harness.backend("c")
            backend.needs_failed_child_recovery_once = True
            backend.session_load_fails = True

            follow_up = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "follow-up"}],
                    "sender_label": "owner",
                    "sender_message_id": "follow-up-id",
                },
            )
            assert follow_up.json() == {
                "fate": "refused",
                "refusal_reason": "session_did_not_load",
            }

        async with harness.client() as refreshed_client:
            events = (
                await refreshed_client.get("/api/conversation/conversations/c/events")
            ).json()["events"]

        assert [row["kind"] for row in events] == [
            "prompt",
            "turn_ended",
            "prompt_delivery_refused",
        ]
        assert events[-1]["payload"] == {
            "text": "follow-up",
            "sender_label": "owner",
            "mode": "queue",
            "refusal_reason": "session_did_not_load",
            "sender_message_id": "follow-up-id",
        }
        assert backend.written_texts == ["first"]
        assert not any(row["kind"] == "agent_message" for row in events)

    _run(exercise)


def test_what_a_sender_minted_reaches_the_row_its_message_becomes(harness: _Harness) -> None:
    """A browser draws its message the moment it is sent, so it has to know its own again.

    A sent message becomes exactly one of three rows, and the id the sender minted is on
    whichever one it becomes. The instant belongs to the delivery, so it rides the prompt.
    """

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            delivered = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "first"}],
                    "sender_label": "owner",
                    "sender_message_id": "m-1",
                    "sent_at_unix_milliseconds": 1_700_000_000_123,
                },
            )
            assert delivered.json() == {"fate": "started"}

            held = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held"}],
                    "sender_label": "owner",
                    "sender_message_id": "m-2",
                    "sent_at_unix_milliseconds": 1_700_000_000_456,
                },
            )
            assert held.json() == {"fate": "queued", "queue_position": 1}

            # The agent frees up, and the held message turns out not to be writable.
            harness.backend("c").write_fails = True
            await harness.complete_turn("c")

            payloads = {
                row["kind"]: row["payload"]
                for row in (
                    await client.get("/api/conversation/conversations/c/events")
                ).json()["events"]
            }
            assert payloads["prompt"] == {
                "text": "first",
                "sender_label": "owner",
                "mode": "queue",
                "sender_message_id": "m-1",
                "sent_at_unix_milliseconds": 1_700_000_000_123,
            }
            assert payloads["prompt_delivery_refused"] == {
                "text": "held",
                "sender_label": "owner",
                "mode": "queue",
                "refusal_reason": "write_to_backend_failed",
                "sender_message_id": "m-2",
            }

            await _start(client, "k")
            await client.post(
                "/api/conversation/conversations/k/send",
                json={
                    "content": [{"piece": "text", "text": "running"}],
                    "sender_label": "owner",
                },
            )
            await client.post(
                "/api/conversation/conversations/k/send",
                json={
                    "content": [{"piece": "text", "text": "never ran"}],
                    "sender_label": "owner",
                    "sender_message_id": "m-3",
                },
            )
            await client.post("/api/conversation/conversations/k/kill")
            await harness.settle()

            assert [
                row["payload"]
                for row in (
                    await client.get("/api/conversation/conversations/k/events")
                ).json()["events"]
                if row["kind"] == "prompt_discarded"
            ] == [{"text": "never ran", "sender_label": "owner", "sender_message_id": "m-3"}]

    _run(exercise)


def test_user_input_replays_after_refresh_and_posts_a_complete_answer_map(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "work"}],
                    "sender_label": "owner",
                },
            )
            request = await harness.request_user_input("c")

            view = (await client.get("/api/conversation/conversations/c")).json()
            assert view["pending_permission_ask"] is None
            assert view["pending_user_input"]["request_id"] == request.request_id
            question_ids = [
                question["question_id"]
                for question in view["pending_user_input"]["questions"]
            ]
            assert question_ids == [
                "scope",
                "timing",
            ]

            response = await client.post(
                "/api/conversation/conversations/c/user-input-answers",
                json={
                    "request_id": request.request_id,
                    "answers": {
                        "scope": {"answers": ["Backend", "Frontend"]},
                        "timing": {"answers": ["Tomorrow"]},
                    },
                },
            )
            assert response.json() == {"landed": True}
            assert harness.backend("c").user_input_answers[request.request_id] == (
                UserInputAnswer("scope", ("Backend", "Frontend")),
                UserInputAnswer("timing", ("Tomorrow",)),
            )
            refreshed = (await client.get("/api/conversation/conversations/c")).json()
            assert refreshed["pending_user_input"] is None

    _run(exercise)


def test_a_waiting_message_can_be_taken_back_by_the_name_its_sender_gave_it(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "incumbent"}],
                    "sender_label": "owner",
                },
            )
            queued = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held"}],
                    "sender_label": "owner",
                    "sender_message_id": "message-one",
                },
            )
            assert queued.json()["fate"] == "queued"
            held = (await client.get("/api/conversation/conversations/c")).json()[
                "held_prompts"
            ]
            assert len(held) == 1
            assert held[0]["sender_message_id"] == "message-one"

            discarded = await client.delete(
                f"/api/conversation/conversations/c/held-prompts/{held[0]['held_prompt_id']}"
            )
            assert discarded.status_code == 200
            assert discarded.json() == {"discarded": True}
            assert (await client.get("/api/conversation/conversations/c")).json()[
                "held_prompts"
            ] == []
            # It reached no backend on the way in, so it reaches none on the way out.
            assert harness.backend("c").written_texts == ["incumbent"]

            # Asking again finds nothing, which is an answer rather than an error.
            again = await client.delete(
                f"/api/conversation/conversations/c/held-prompts/{held[0]['held_prompt_id']}"
            )
            assert again.status_code == 200
            assert again.json() == {"discarded": False}

    _run(exercise)


def test_a_waiting_message_can_be_promoted_by_its_server_owned_id(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "incumbent"}],
                    "sender_label": "owner",
                },
            )
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held"}],
                    "sender_label": "owner",
                    "sender_message_id": "sender-one",
                    "sent_at_unix_milliseconds": 1234,
                },
            )
            held = (await client.get("/api/conversation/conversations/c")).json()[
                "held_prompts"
            ][0]
            assert held == {
                "held_prompt_id": held["held_prompt_id"],
                "text": "held",
                "sender_label": "owner",
                "sender_message_id": "sender-one",
                    "sent_at_unix_milliseconds": 1234,
                    "queue_reason": "requested",
                }

            promoted = await client.post(
                f"/api/conversation/conversations/c/held-prompts/{held['held_prompt_id']}/promote",
                json={"mode": "send_now"},
            )

            assert promoted.json() == {"promoted": True, "fate": "started"}
            assert harness.backend("c").written_texts == ["incumbent", "held"]
            assert harness.backend("c").cancellations == 1
            assert (await client.get("/api/conversation/conversations/c")).json()[
                "held_prompts"
            ] == []
            again = await client.post(
                f"/api/conversation/conversations/c/held-prompts/{held['held_prompt_id']}/promote",
                json={"mode": "send_now"},
            )
            assert again.json() == {"promoted": False}

    _run(exercise)


def test_the_tail_signals_that_held_prompts_changed(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "incumbent"}],
                    "sender_label": "owner",
                },
            )
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held before subscribe"}],
                    "sender_label": "owner",
                },
            )
            async with _EventStreamDrive(
                harness.app, "/api/conversation/conversations/c/tail", "after=1"
            ) as stream:
                await stream.wait_until_watching(harness.live_tail)
                # The tail registered after the enqueue, so its initial wake closes the
                # exact gap where the ephemeral mutation frame was unavailable.
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )
                held = (await client.get(
                    "/api/conversation/conversations/c"
                )).json()["held_prompts"][0]
                await client.delete(
                    f"/api/conversation/conversations/c/held-prompts/{held['held_prompt_id']}"
                )
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )

            # An empty queue still wakes a new binder. This closes the inverse race:
            # its earlier view may have contained the row that was just discarded.
            latest = (await client.get(
                "/api/conversation/conversations/c"
            )).json()["latest_sequence"]
            async with _EventStreamDrive(
                harness.app,
                "/api/conversation/conversations/c/tail",
                f"after={latest}",
            ) as empty_stream:
                await empty_stream.wait_until_watching(harness.live_tail)
                assert await empty_stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )

    _run(exercise)


def test_an_answer_lands_once_and_then_has_nothing_left_to_land_on(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "work"}],
                    "sender_label": "owner",
                },
            )
            ask_id = await harness.raise_permission_ask("c")

            landed = await client.post(
                "/api/conversation/conversations/c/permission-answers",
                json={"ask_id": ask_id, "option_id": "allow-once"},
            )
            assert landed.status_code == 200
            assert landed.json() == {"landed": True}
            assert harness.backend("c").permission_answers == {ask_id: "allow-once"}

            again = await client.post(
                "/api/conversation/conversations/c/permission-answers",
                json={"ask_id": ask_id, "option_id": "deny"},
            )
            assert again.json() == {"landed": False}

            stale = await client.post(
                "/api/conversation/conversations/c/permission-answers",
                json={"ask_id": "no-such-ask", "option_id": "allow-once"},
            )
            assert stale.json() == {"landed": False}
            assert harness.backend("c").permission_answers == {ask_id: "allow-once"}

            view = (await client.get("/api/conversation/conversations/c")).json()
            assert view["pending_permission_ask"] is None

    _run(exercise)


def test_interrupting_frees_what_was_held_and_killing_throws_it_away(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "interrupted")
            await client.post(
                "/api/conversation/conversations/interrupted/send",
                json={
                    "content": [{"piece": "text", "text": "incumbent"}],
                    "sender_label": "owner",
                },
            )
            await client.post(
                "/api/conversation/conversations/interrupted/send",
                json={
                    "content": [{"piece": "text", "text": "held"}],
                    "sender_label": "owner",
                },
            )

            stopped = await client.post("/api/conversation/conversations/interrupted/interrupt")
            await harness.settle()

            assert stopped.status_code == 204
            assert harness.backend("interrupted").written_texts == ["incumbent", "held"]

            await _start(client, "killed")
            await client.post(
                "/api/conversation/conversations/killed/send",
                json={
                    "content": [{"piece": "text", "text": "incumbent"}],
                    "sender_label": "owner",
                },
            )
            await client.post(
                "/api/conversation/conversations/killed/send",
                json={
                    "content": [{"piece": "text", "text": "held"}],
                    "sender_label": "owner",
                },
            )

            killed = await client.post("/api/conversation/conversations/killed/kill")
            await harness.settle()

            assert killed.status_code == 204
            assert harness.backend("killed").written_texts == ["incumbent"]
            kinds = [
                event["kind"]
                for event in (
                    await client.get("/api/conversation/conversations/killed/events")
                ).json()["events"]
            ]
            assert kinds == ["prompt", "prompt_discarded", "turn_ended"]

    _run(exercise)


# --- reading the record ---------------------------------------------------------------------


def test_the_rows_after_a_position_come_back_in_order_and_decoded(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "work"}],
                    "sender_label": "owner",
                    "mode": "queue",
                },
            )
            await harness.complete_turn("c")

            everything = (
                await client.get("/api/conversation/conversations/c/events")
            ).json()["events"]
            assert [event["sequence"] for event in everything] == [1, 2]
            assert [event["kind"] for event in everything] == ["prompt", "turn_ended"]
            assert everything[0]["payload"] == {
                # The compact form: a message that is only words is stored, and comes
                # back, exactly as it always was. Nothing ordinary grew.
                "text": "work",
                "sender_label": "owner",
                "mode": "queue",
            }
            assert everything[1]["payload"] == {"ending": "completed", "error_summary": None}

            after_the_first = (
                await client.get("/api/conversation/conversations/c/events", params={"after": 1})
            ).json()["events"]
            assert [event["sequence"] for event in after_the_first] == [2]

    _run(exercise)


def test_claude_legacy_image_detail_stays_in_rows_but_not_public_replays(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        legacy_detail = json.dumps(
            [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "A" * 810_000,
                    },
                }
            ],
            sort_keys=True,
        )
        async with harness.client() as client:
            await _start(client, "claude-images", backend_key="claude")
            for payload in (
                ToolCallStartedEventPayload(
                    tool_call_id="view-1",
                    title="View image",
                    tool_kind="view_image",
                    detail="image.png",
                ),
                ToolCallFinishedEventPayload(
                    tool_call_id="view-1",
                    tool_call_status=ToolCallStatus.completed,
                    detail=legacy_detail,
                ),
                AgentMessageEventPayload(
                    content=text_message_content("The image is clear.")
                ),
            ):
                await harness.store.append_event("claude-images", payload)

            raw_rows = await harness.store.read_events_after("claude-images", 0)
            assert isinstance(raw_rows[1].payload, ToolCallFinishedEventPayload)
            assert raw_rows[1].payload.detail == legacy_detail

            fetched_response = await client.get(
                "/api/conversation/conversations/claude-images/events"
            )
            fetched = fetched_response.json()["events"]
            assert len(fetched_response.content) < len(legacy_detail) // 100
            assert [event["sequence"] for event in fetched] == [1, 2, 3]
            assert [event["kind"] for event in fetched] == [
                "tool_call_started",
                "tool_call_finished",
                "agent_message",
            ]
            assert fetched[0]["payload"] == {
                "tool_call_id": "view-1",
                "title": "View image",
                "tool_kind": "view_image",
                "detail": "image.png",
            }
            assert fetched[1]["payload"] == {
                "tool_call_id": "view-1",
                "tool_call_status": "completed",
                "detail": None,
            }
            assert fetched[2]["payload"] == {"text": "The image is clear."}

            async with _EventStreamDrive(
                harness.app,
                "/api/conversation/conversations/claude-images/tail",
                "after=0",
            ) as stream:
                replayed = [await stream.next_named_frame() for _ in range(3)]
                assert [name for name, _event in replayed] == [
                    COMMITTED_EVENT_STREAM_NAME,
                    COMMITTED_EVENT_STREAM_NAME,
                    COMMITTED_EVENT_STREAM_NAME,
                ]
                assert [event for _name, event in replayed] == fetched
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )

                live_row = await harness.store.append_event(
                    "claude-images",
                    ToolCallFinishedEventPayload(
                        tool_call_id="view-2",
                        tool_call_status=ToolCallStatus.completed,
                        detail=legacy_detail,
                    ),
                )
                harness.live_tail.publish_event(live_row)
                name, live_event = await stream.next_named_frame()
                assert name == COMMITTED_EVENT_STREAM_NAME
                assert live_event["sequence"] == 4
                assert live_event["payload"] == {
                    "tool_call_id": "view-2",
                    "tool_call_status": "completed",
                    "detail": None,
                }

            raw_rows = await harness.store.read_events_after("claude-images", 0)
            assert isinstance(raw_rows[-1].payload, ToolCallFinishedEventPayload)
            assert raw_rows[-1].payload.detail == legacy_detail

    _run(exercise)


def test_public_replays_keep_non_claude_and_unrecognized_json_detail(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        image_detail = json.dumps(
            [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "image-bytes",
                    },
                }
            ]
        )
        controls = (
            ("codex-image-json", "codex", image_detail),
            (
                "claude-url-image",
                "claude",
                json.dumps(
                    [
                        {
                            "type": "image",
                            "source": {
                                "type": "url",
                                "url": "https://example.test/image",
                            },
                        }
                    ]
                ),
            ),
            (
                "claude-text-json",
                "claude",
                json.dumps([{"type": "text", "text": "readable result"}]),
            ),
            (
                "claude-image-caption",
                "claude",
                json.dumps(
                    [
                        {
                            "type": "image",
                            "text": "readable caption",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "image-bytes",
                            },
                        }
                    ]
                ),
            ),
            (
                "claude-image-source-extra",
                "claude",
                json.dumps(
                    [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "image-bytes",
                                "filename": "result.png",
                            },
                        }
                    ]
                ),
            ),
        )
        async with harness.client() as client:
            for conversation_id, backend_key, detail in controls:
                await _start(client, conversation_id, backend_key=backend_key)
                await harness.store.append_event(
                    conversation_id,
                    ToolCallFinishedEventPayload(
                        tool_call_id="tool-1",
                        tool_call_status=ToolCallStatus.completed,
                        detail=detail,
                    ),
                )
                events = (
                    await client.get(
                        f"/api/conversation/conversations/{conversation_id}/events"
                    )
                ).json()["events"]
                assert events[0]["payload"]["detail"] == detail

    _run(exercise)


def test_a_public_read_carries_the_start_of_a_long_tool_output_and_says_so(
    harness: _Harness,
) -> None:
    """An open pays for the lines it draws, not for output nobody has opened.

    The events read and the tail replay are the two ways an open arrives, so both are
    exercised here, and the row that was shortened says that it was.
    """

    async def exercise() -> None:
        whole = "".join(f"line {number}\n" for number in range(4_000))
        assert len(whole) > PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS
        short = "up to date\n"
        async with harness.client() as client:
            await _start(client, "long-output")
            for payload in (
                ToolCallFinishedEventPayload(
                    tool_call_id="call-1",
                    tool_call_status=ToolCallStatus.completed,
                    detail=whole,
                ),
                ToolCallFinishedEventPayload(
                    tool_call_id="call-2",
                    tool_call_status=ToolCallStatus.completed,
                    detail=short,
                ),
            ):
                await harness.store.append_event("long-output", payload)

            response = await client.get(
                "/api/conversation/conversations/long-output/events"
            )
            fetched = response.json()["events"]
            assert fetched[0]["payload"] == {
                "tool_call_id": "call-1",
                "tool_call_status": "completed",
                "detail": whole[:PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS],
                "detail_capped": True,
            }
            # A short output is the whole of what the tool printed, so nothing says it
            # was shortened and the fold has nothing to ask for.
            assert fetched[1]["payload"] == {
                "tool_call_id": "call-2",
                "tool_call_status": "completed",
                "detail": short,
            }
            assert len(response.content) < len(whole) // 10

            async with _EventStreamDrive(
                harness.app,
                "/api/conversation/conversations/long-output/tail",
                "after=0",
            ) as stream:
                replayed = [await stream.next_named_frame() for _ in range(2)]
                assert [event for _name, event in replayed] == fetched

            # The record kept the whole of it, and that is what the fold asks for.
            whole_response = await client.get(
                "/api/conversation/conversations/long-output/events/1/detail"
            )
            assert whole_response.json() == {"detail": whole}
            stored = await harness.store.read_events_after("long-output", 0)
            assert isinstance(stored[0].payload, ToolCallFinishedEventPayload)
            assert stored[0].payload.detail == whole

    _run(exercise)


def test_the_whole_detail_of_something_that_is_not_a_finished_tool_call_is_a_404(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await harness.store.append_event(
                "c", AgentMessageEventPayload(content=text_message_content("hello"))
            )
            base = "/api/conversation/conversations"
            assert (await client.get(f"{base}/c/events/1/detail")).status_code == 404
            assert (await client.get(f"{base}/c/events/9/detail")).status_code == 404
            assert (await client.get(f"{base}/nope/events/1/detail")).status_code == 404

    _run(exercise)


def test_the_whole_detail_of_a_legacy_claude_image_row_is_still_nothing(
    harness: _Harness,
) -> None:
    """The route the fold asks on is a public read, so it hides what the others hide."""

    async def exercise() -> None:
        legacy_detail = json.dumps(
            [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "A" * 4_000,
                    },
                }
            ],
            sort_keys=True,
        )
        async with harness.client() as client:
            await _start(client, "claude-image-detail", backend_key="claude")
            await harness.store.append_event(
                "claude-image-detail",
                ToolCallFinishedEventPayload(
                    tool_call_id="view-1",
                    tool_call_status=ToolCallStatus.completed,
                    detail=legacy_detail,
                ),
            )
            whole = await client.get(
                "/api/conversation/conversations/claude-image-detail/events/1/detail"
            )
            assert whole.json() == {"detail": None}

    _run(exercise)


def test_the_tail_replays_then_carries_on_with_no_gap_and_no_repeat(
    harness: _Harness,
) -> None:
    """The join is the whole point of the tail, so it is made to happen on purpose.

    Two rows are committed in the moment between the watch being registered and the record
    being read back — the exact window a naive tail loses rows in, or sends twice. They are
    in the replay *and* on the watch, and the reader must show each of them once.
    """

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            for text in ("row one", "row two"):
                stored = await harness.store.append_event(
                    "c", AgentMessageEventPayload(content=text_message_content(text))
                )
                harness.live_tail.publish_event(stored)

            committed_during_the_replay = False
            read_events_after = harness.store.read_events_after

            async def commit_while_the_replay_is_being_read(
                conversation_id: str, after_sequence: int
            ) -> Any:
                nonlocal committed_during_the_replay
                if not committed_during_the_replay:
                    committed_during_the_replay = True
                    for text in ("row three", "row four"):
                        stored = await harness.store.append_event(
                            conversation_id,
                            AgentMessageEventPayload(
                                content=text_message_content(text)
                            ),
                        )
                        harness.live_tail.publish_event(stored)
                return await read_events_after(conversation_id, after_sequence)

            harness.store.read_events_after = commit_while_the_replay_is_being_read  # type: ignore[method-assign]

            async with _EventStreamDrive(
                harness.app, "/api/conversation/conversations/c/tail", "after=0"
            ) as stream:
                seen: list[tuple[int, str]] = []
                for _ in range(4):
                    name, payload = await stream.next_named_frame()
                    assert name == COMMITTED_EVENT_STREAM_NAME
                    seen.append((payload["sequence"], payload["payload"]["text"]))

                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )

                # Committed after the replay was read: it can only arrive live.
                stored = await harness.store.append_event(
                    "c", AgentMessageEventPayload(content=text_message_content("row five"))
                )
                harness.live_tail.publish_event(stored)
                name, payload = await stream.next_named_frame()
                seen.append((payload["sequence"], payload["payload"]["text"]))

                assert seen == [
                    (1, "row one"),
                    (2, "row two"),
                    (3, "row three"),
                    (4, "row four"),
                    (5, "row five"),
                ]
                assert len({sequence for sequence, _text in seen}) == 5

    _run(exercise)


def test_the_tail_shows_text_that_has_not_finished_arriving_and_never_stores_it(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "work"}],
                    "sender_label": "owner",
                },
            )

            async with _EventStreamDrive(
                harness.app, "/api/conversation/conversations/c/tail", "after=1"
            ) as stream:
                await stream.wait_until_watching(harness.live_tail)
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )
                await harness.stream_agent_text("c", "half a th")
                await harness.stream_agent_text("c", "ought")

                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "agent_message_delta", "text_delta": "half a th"},
                )
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "agent_message_delta", "text_delta": "ought"},
                )

                await harness.complete_turn("c")
                name, payload = await stream.next_named_frame()
                assert name == COMMITTED_EVENT_STREAM_NAME
                assert payload["kind"] == "turn_ended"

            # The pieces were shown and forgotten: nothing about them is in the record.
            rows = (await client.get("/api/conversation/conversations/c/events")).json()
            assert [event["kind"] for event in rows["events"]] == ["prompt", "turn_ended"]

    _run(exercise)


def test_the_tail_shows_a_tool_call_getting_on_with_it_and_keeps_no_row_for_it(
    harness: _Harness,
) -> None:
    """The call starting is a row; what it says while it runs is only ever shown."""

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "work"}],
                    "sender_label": "owner",
                },
            )

            async with _EventStreamDrive(
                harness.app, "/api/conversation/conversations/c/tail", "after=1"
            ) as stream:
                await stream.wait_until_watching(harness.live_tail)
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )
                await harness.start_tool_call("c", "t-9")
                await harness.stream_tool_output("c", "t-9", "total 0\n")
                await harness.stream_tool_output("c", "t-9", "halfway")
                await harness.finish_tool_call("c", "t-9")

                name, payload = await stream.next_named_frame()
                assert (name, payload["kind"]) == (COMMITTED_EVENT_STREAM_NAME, "tool_call_started")
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "tool_call_progress", "tool_call_id": "t-9", "detail": "total 0\n"},
                )
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "tool_call_progress", "tool_call_id": "t-9", "detail": "halfway"},
                )
                name, payload = await stream.next_named_frame()
                assert (name, payload["kind"]) == (
                    COMMITTED_EVENT_STREAM_NAME,
                    "tool_call_finished",
                )

            rows = (await client.get("/api/conversation/conversations/c/events")).json()
            assert [event["kind"] for event in rows["events"]] == [
                "prompt",
                "tool_call_started",
                "tool_call_finished",
            ]

    _run(exercise)


def test_the_tail_says_the_model_is_thinking_without_saying_what(
    harness: _Harness,
) -> None:
    """The one thing a turn can show before it has produced anything visible.

    The frame carries no fields at all — there is nothing in it to leak — and it is
    exactly the shape the pane reads.
    """

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "think hard about this"}],
                    "sender_label": "owner",
                },
            )

            async with _EventStreamDrive(
                harness.app, "/api/conversation/conversations/c/tail", "after=1"
            ) as stream:
                await stream.wait_until_watching(harness.live_tail)
                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "held_prompts_changed"},
                )
                await harness.model_is_thinking("c")

                assert await stream.next_named_frame() == (
                    LIVE_FRAME_STREAM_NAME,
                    {"frame": "model_thinking"},
                )

            # It was shown, and the record does not know it ever happened.
            rows = (await client.get("/api/conversation/conversations/c/events")).json()
            assert [event["kind"] for event in rows["events"]] == ["prompt"]

    _run(exercise)


def test_a_quiet_tail_is_kept_alive_by_a_comment(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")

        async with _EventStreamDrive(
            harness.app, "/api/conversation/conversations/c/tail", "after=0"
        ) as stream:
            assert await stream.next_named_frame() == (
                LIVE_FRAME_STREAM_NAME,
                {"frame": "held_prompts_changed"},
            )
            assert (await stream.next_frame()).startswith(":")

    _run(exercise)


def test_shutting_down_closes_every_open_tail(harness: _Harness) -> None:
    """A tail only ends when its browser leaves, so a server going away ends it itself."""

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")

        async with _EventStreamDrive(
            harness.app, "/api/conversation/conversations/c/tail", "after=0"
        ) as stream:
            await stream.next_frame()
            assert harness.live_tail.open_subscription_count() == 1

            harness.live_tail.close_all_subscriptions()
            for _ in range(20):
                await asyncio.sleep(0)
            assert harness.live_tail.open_subscription_count() == 0

    _run(exercise)


def test_a_watcher_that_stops_reading_has_its_watch_closed_rather_than_grown(
    harness: _Harness,
) -> None:
    """A browser that has stopped reading must not cost this process memory forever.

    Closing the watch is the kind thing as well as the safe thing: a closed tail is the
    reconnect path, and reconnecting asks for everything after the last row seen. Dropping
    items quietly instead would leave a hole in the middle that nothing ever fills.
    """
    del harness

    async def exercise() -> None:
        hub = ConversationLiveTail()
        watching = hub.subscribe("c")

        for index in range(MAXIMUM_HELD_TAIL_ITEMS + 5):
            hub.publish_frame("c", AgentMessageDeltaFrame(text_delta=str(index)))

        assert hub.open_subscription_count() == 0

        # The reader finishes rather than hanging, holding everything up to the limit.
        shown = [item async for item in watching]
        assert len(shown) == MAXIMUM_HELD_TAIL_ITEMS
        assert shown[0] == AgentMessageDeltaFrame(text_delta="0")

    _run(exercise)


def test_a_tail_is_closed_by_the_same_door_that_closes_the_change_stream(
    harness: _Harness,
) -> None:
    """The door that matters is the signal handler's, not the lifespan's.

    A server draining its connections never reaches the lifespan while a stream is still
    open, so a tail that was only closed there would hold the shutdown open forever. It
    is closed from the same call the change stream is, on a thread that is not its own.
    """

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")

        streams_before = sse.open_change_stream_count()
        async with _EventStreamDrive(
            harness.app, "/api/conversation/conversations/c/tail", "after=0"
        ) as stream:
            await stream.wait_until_watching(harness.live_tail)
            assert sse.open_change_stream_count() == streams_before + 1

            # Exactly as the process's signal handler calls it: from another thread.
            await asyncio.to_thread(sse.close_open_change_streams)

            # The watch is ended by the closer; the stream lets go of its place in the
            # register when it next runs and finds the watch over. Both happen without
            # anybody asking again, which is what a shutdown needs — so both are waited
            # for rather than assumed to have happened by now.
            for _ in range(200):
                if (
                    harness.live_tail.open_subscription_count() == 0
                    and sse.open_change_stream_count() == streams_before
                ):
                    break
                await asyncio.sleep(0.005)

            assert harness.live_tail.open_subscription_count() == 0
            assert sse.open_change_stream_count() == streams_before

    _run(exercise)


# --- the backends on this machine -------------------------------------------------------------


class _UsageAnswer:
    def __init__(self, result: BackendUsageResult) -> None:
        self.result = result

    async def refresh(self) -> BackendUsageResult:
        return self.result


class _SnapshotAnswers(BackendSnapshotService):
    def __init__(self, snapshots: tuple[BackendSnapshot, ...]) -> None:
        self.snapshots_answer = snapshots

    async def snapshots(self, *, refresh: bool = False) -> tuple[BackendSnapshot, ...]:
        assert refresh is False
        return self.snapshots_answer


class _OrderedUsageAnswers(BackendUsageService):
    def __init__(self) -> None:
        super().__init__({})
        self.started: set[ConversationBackendKey] = set()
        self.completed: set[ConversationBackendKey] = set()

    async def refresh(self, backend_key: ConversationBackendKey) -> BackendUsageResult:
        self.started.add(backend_key)
        self.completed.add(backend_key)
        return BackendUsageResult(
            backend_key=backend_key,
            outcome=BackendUsageOutcome.unavailable,
            detail="No usage source.",
        )


class _SnapshotsAfterUsageStart(_SnapshotAnswers):
    def __init__(
        self,
        snapshots: tuple[BackendSnapshot, ...],
        release_snapshots: asyncio.Event,
    ) -> None:
        super().__init__(snapshots)
        self.release_snapshots = release_snapshots
        self.started = asyncio.Event()
        self.refresh_arguments: list[bool] = []

    async def snapshots(self, *, refresh: bool = False) -> tuple[BackendSnapshot, ...]:
        self.refresh_arguments.append(refresh)
        self.started.set()
        await self.release_snapshots.wait()
        return self.snapshots_answer


class _UsageAnswersThatWait(BackendUsageService):
    def __init__(self) -> None:
        super().__init__({})
        self.started: set[ConversationBackendKey] = set()
        self.cancelled: set[ConversationBackendKey] = set()

    async def refresh(self, backend_key: ConversationBackendKey) -> BackendUsageResult:
        self.started.add(backend_key)
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.add(backend_key)
        raise AssertionError("usage wait was released")


class _SnapshotFailure(BackendSnapshotService):
    async def snapshots(self, *, refresh: bool = False) -> tuple[BackendSnapshot, ...]:
        del refresh
        raise RuntimeError("snapshot acquisition stopped")


def test_backend_refresh_starts_usage_before_ordinary_snapshot_acquisition(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        snapshots = tuple(
            BackendSnapshot(
                backend_key=backend_key,
                installed=False,
                executable_path=None,
                version=None,
                identity=None,
                available_models=(),
                reasoning_effort_options=(),
                default_model_id=None,
                default_reasoning_effort=None,
                update_advisory=None,
                diagnoses=(),
            )
            for backend_key in ConversationBackendKey
        )
        usage = _OrderedUsageAnswers()
        release_snapshots = asyncio.Event()
        snapshot_answers = _SnapshotsAfterUsageStart(snapshots, release_snapshots)
        runtime = replace(
            harness.runtime,
            backend_snapshots=snapshot_answers,
            backend_usage=usage,
        )

        refreshing = asyncio.create_task(conversation_api.refresh_backends(runtime))
        for _ in range(20):
            if snapshot_answers.started.is_set():
                break
            await asyncio.sleep(0)
        completed_before_snapshot = set(usage.completed)
        release_snapshots.set()
        response = await refreshing

        assert snapshot_answers.started.is_set()
        assert completed_before_snapshot == set(ConversationBackendKey)
        assert snapshot_answers.refresh_arguments == [False]
        assert [item["backend_key"] for item in response["usage_outcomes"]] == [
            backend_key.value for backend_key in ConversationBackendKey
        ]
        assert [item["backend_key"] for item in response["backends"]] == [
            backend_key.value for backend_key in ConversationBackendKey
        ]

    _run(exercise)


def test_backend_refresh_cancels_provider_reads_when_snapshot_acquisition_fails(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        usage = _UsageAnswersThatWait()
        runtime = replace(
            harness.runtime,
            backend_snapshots=_SnapshotFailure(),
            backend_usage=usage,
        )

        with pytest.raises(RuntimeError, match="snapshot acquisition stopped"):
            await conversation_api.refresh_backends(runtime)

        assert usage.started == set(ConversationBackendKey)
        assert usage.cancelled == set(ConversationBackendKey)

    _run(exercise)


def test_backend_refresh_resolves_scopes_and_keeps_cached_usage_after_failure(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        state = BackendStateStore(str(harness.db_path))
        reset = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
        old_observation = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
        new_observation = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
        old_claude = BackendUsageResult(
            backend_key=ConversationBackendKey.claude,
            outcome=BackendUsageOutcome.succeeded,
            observed_at=old_observation,
            windows=(
                BackendUsageWindow(
                    kind=BackendUsageWindowKind.seven_day,
                    used_percent=20,
                    resets_at=reset,
                ),
            ),
        )
        state.keep_successful_usage(old_claude)

        snapshots = tuple(
            BackendSnapshot(
                backend_key=backend_key,
                installed=True,
                executable_path=f"/bin/{backend_key.value}",
                version="1.0.0",
                identity=None,
                available_models=(
                    BackendModel(model_id="spark[1m]", display_name="Spark 5 (1M)"),
                ),
                reasoning_effort_options=(),
                default_model_id="spark[1m]",
                default_reasoning_effort=None,
                update_advisory=None,
                diagnoses=(),
            )
            for backend_key in ConversationBackendKey
        )
        codex = BackendUsageResult(
            backend_key=ConversationBackendKey.codex,
            outcome=BackendUsageOutcome.succeeded,
            observed_at=new_observation,
            windows=(
                BackendUsageWindow(
                    kind=BackendUsageWindowKind.seven_day,
                    model_scope="Spark",
                    used_percent=8,
                    resets_at=reset,
                ),
                BackendUsageWindow(
                    kind=BackendUsageWindowKind.seven_day,
                    model_scope="gpt-reserve",
                    used_percent=9,
                    resets_at=reset,
                ),
            ),
        )
        claude_failure = BackendUsageResult(
            backend_key=ConversationBackendKey.claude,
            outcome=BackendUsageOutcome.failed,
            detail="Claude usage could not be refreshed. Try again.",
        )
        runtime = replace(
            harness.runtime,
            backend_snapshots=_SnapshotAnswers(snapshots),
            backend_usage=BackendUsageService(
                {
                    ConversationBackendKey.codex: _UsageAnswer(codex),
                    ConversationBackendKey.claude: _UsageAnswer(claude_failure),
                }
            ),
            backend_state=state,
        )

        response = await conversation_api.refresh_backends(runtime)

        assert [item["outcome"] for item in response["usage_outcomes"]] == [
            "unavailable",
            "succeeded",
            "failed",
        ]
        by_backend = {item["backend_key"]: item for item in response["backends"]}
        assert by_backend["codex"]["cached_usage"]["windows"] == [
            {
                "kind": "seven_day",
                "used_percent": 8,
                "resets_at": "2026-09-17T12:00:00Z",
                "model_id": "spark[1m]",
            }
        ]
        assert by_backend["claude"]["cached_usage"]["observed_at"] == (
            "2026-09-09T12:00:00Z"
        )

    _run(exercise)


# --- the wiring in the real application ---------------------------------------------------------


def test_the_application_serves_the_conversation_system_and_puts_it_away(
    tmp_path: Path,
) -> None:
    """The additive wiring, proved through the real app: built on start, gone on stop."""
    db_path = tmp_path / "wired.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)}
    )

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    app = create_app(config, build_clock(config), conn_factory)
    with TestClient(app) as client:
        assert client.get("/api/conversation/conversations/nobody").status_code == 404

        created = client.post(
            "/api/conversation/conversations",
            json={"conversation_id": "wired", "model": "a-model", "backend_key": "codex"},
        )
        assert created.status_code == 201
        assert created.json()["backend_key"] == "codex"

        view = client.get("/api/conversation/conversations/wired")
        assert view.status_code == 200
        # Creating a conversation spawns nothing, which is why this test can run against
        # the real backends without an agent starting anywhere.
        assert view.json()["is_running"] is False

        events = client.get("/api/conversation/conversations/wired/events")
        assert events.json() == {"events": []}

        # The worker path and the browser's conversation are the same system. A worker's
        # prompt goes into a real conversation, not a stand-in beside it.
        assert app.state.conversation_system is app.state.conversation.system

    assert app.state.conversation is None


# --- a message that carries more than words --------------------------------------------------


# One real PNG, small enough to read: a 1x1 image, which is a genuine file rather than a
# few bytes pretending to be one.
A_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
MALFORMED_CLAIMED_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    + (1).to_bytes(4, "big")
    + (1).to_bytes(4, "big")
    + b"\x08\x02\x00\x00\x00"
    + b"\x00\x00\x00\x00"
    + b"not image data"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _png_with_total_bytes(total_bytes: int) -> bytes:
    """A real tiny PNG carrying an ignored ancillary chunk to reach an exact wire size."""
    data_length = total_bytes - len(A_TINY_PNG) - 12
    assert data_length >= 0
    kind = b"tEXt"
    data = b"x" * data_length
    chunk = (
        data_length.to_bytes(4, "big")
        + kind
        + data
        + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
    )
    return A_TINY_PNG[:-12] + chunk + A_TINY_PNG[-12:]


def test_a_picture_sent_with_a_message_is_kept_and_the_row_names_what_was_kept(
    harness: _Harness,
) -> None:
    """The whole path in one exercise: bytes in, a file kept, a row that names it.

    The bytes ride with the message they belong to — there is no upload of their own — and
    what the record holds is the file this system kept, not the bytes. That is what lets
    one value serve the record, the backend and the browser.
    """

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            sent = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [
                        {"piece": "text", "text": "look at this"},
                        {
                            "piece": "image",
                            "data": base64.b64encode(A_TINY_PNG).decode("ascii"),
                            # A client claim is only a hint. The bytes authoritatively say
                            # PNG, and that canonical type is what every downstream path sees.
                            "media_type": "image/jpeg",
                            "file_name": "screenshot.png",
                        },
                    ],
                    "sender_label": "owner",
                },
            )
            assert sent.json() == {"fate": "started"}

            rows = (
                await client.get("/api/conversation/conversations/c/events")
            ).json()["events"]
            payload = rows[0]["payload"]
            assert payload["content"][0] == {"piece": "text", "text": "look at this"}
            picture = payload["content"][1]
            assert picture["piece"] == "image"
            assert picture["media_type"] == "image/png"
            assert picture["file_name"] == "screenshot.png"
            # The bytes are not in the row. What is in the row is where they went.
            assert "data" not in picture

            # And they really are on disk, under this conversation, byte for byte.
            kept = await harness.message_files.read("c", picture["stored_file_id"])
            assert kept == A_TINY_PNG

            # The browser fetches them from the route, with the type the record recorded
            # rather than one guessed from the bytes at serving time.
            served = await client.get(
                f"/api/conversation/conversations/c/files/{picture['stored_file_id']}"
            )
            assert served.status_code == 200
            assert served.headers["content-type"] == "image/png"
            assert served.content == A_TINY_PNG

    _run(exercise)


def test_a_data_file_is_validated_kept_and_served_with_server_metadata(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            payload = b'name,value\nanswer,"42"\n'
            sent = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [
                        {
                            "piece": "file",
                            "data": base64.b64encode(payload).decode("ascii"),
                            "media_type": "application/octet-stream",
                            "file_name": "facts.csv",
                        }
                    ],
                    "sender_label": "owner",
                },
            )
            assert sent.json() == {"fate": "started"}

            rows = (await client.get("/api/conversation/conversations/c/events")).json()[
                "events"
            ]
            piece = rows[0]["payload"]["content"][0]
            assert piece == {
                "piece": "file",
                "stored_file_id": piece["stored_file_id"],
                "media_type": "text/csv",
                "file_name": "facts.csv",
                "byte_count": len(payload),
            }
            served = await client.get(
                f"/api/conversation/conversations/c/files/{piece['stored_file_id']}"
            )
            assert served.headers["content-type"] == "text/csv; charset=utf-8"
            assert served.content == payload

    _run(exercise)


def test_a_valid_file_followed_by_an_invalid_file_keeps_nothing(tmp_path: Path) -> None:
    async def exercise() -> None:
        message_files = ConversationMessageFiles(tmp_path / "planner.db")
        with pytest.raises(HTTPException, match="must contain valid JSON"):
            await conversation_message_content(
                message_files,
                "c",
                [
                    SentFilePiece(
                        piece="file",
                        data=base64.b64encode(b"valid text").decode("ascii"),
                        media_type="text/plain",
                        file_name="valid.txt",
                    ),
                    SentFilePiece(
                        piece="file",
                        data=base64.b64encode(b"{no").decode("ascii"),
                        media_type="application/json",
                        file_name="invalid.json",
                    ),
                ],
            )
        assert not list((tmp_path / "files").glob("**/*"))

    _run(exercise)


def test_file_bytes_are_limited_per_piece_before_decode_and_in_aggregate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        monkeypatch.setattr(conversation_api, "MAX_CONVERSATION_MESSAGE_FILE_BYTES", 10)
        message_files = ConversationMessageFiles(tmp_path / "planner.db")

        with pytest.raises(HTTPException, match="files are too large"):
            await conversation_message_content(
                message_files,
                "single",
                [
                    SentFilePiece(
                        piece="file",
                        data="A" * 17,
                        media_type="text/plain",
                        file_name="large.txt",
                    )
                ],
            )

        encoded = base64.b64encode(b"123456").decode("ascii")
        with pytest.raises(HTTPException, match="files are too large"):
            await conversation_message_content(
                message_files,
                "aggregate",
                [
                    SentFilePiece(
                        piece="file",
                        data=encoded,
                        media_type="text/plain",
                        file_name="one.txt",
                    ),
                    SentFilePiece(
                        piece="file",
                        data=encoded,
                        media_type="text/plain",
                        file_name="two.txt",
                    ),
                ],
            )
        assert not list((tmp_path / "files").glob("**/*"))

    _run(exercise)


def test_the_raw_message_envelope_has_an_exact_boundary_and_counts_all_images(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "boundary")
            boundary_png = _png_with_total_bytes(MAX_CONVERSATION_MESSAGE_IMAGE_BYTES)
            accepted = await client.post(
                "/api/conversation/conversations/boundary/send",
                json={
                    "content": [
                        {
                            "piece": "image",
                            "data": base64.b64encode(boundary_png).decode("ascii"),
                            "media_type": "image/png",
                        }
                    ],
                    "sender_label": "owner",
                },
            )
            assert accepted.json() == {"fate": "started"}

            await _start(client, "aggregate")
            half_plus_one = MAX_CONVERSATION_MESSAGE_IMAGE_BYTES // 2 + 1
            image = _png_with_total_bytes(half_plus_one)
            rejected = await client.post(
                "/api/conversation/conversations/aggregate/send",
                json={
                    "content": [
                        {
                            "piece": "image",
                            "data": base64.b64encode(image).decode("ascii"),
                            "media_type": "image/png",
                        },
                        {
                            "piece": "image",
                            "data": base64.b64encode(image).decode("ascii"),
                            "media_type": "image/png",
                        },
                    ],
                    "sender_label": "owner",
                },
            )
            assert rejected.status_code == 422
            assert (
                rejected.json()["detail"]
                == "a conversation message's images are too large"
            )
            assert (
                await client.get("/api/conversation/conversations/aggregate/events")
            ).json() == {"events": []}
            aggregate_files = (
                harness.db_path.parent / "files" / "conversations" / "aggregate"
            )
            assert not aggregate_files.exists()

    _run(exercise)


def test_the_picture_reaches_the_backend_and_not_just_the_record(harness: _Harness) -> None:
    """A row is not delivery. The message the agent was handed carries the picture too."""

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [
                        {"piece": "text", "text": "look at this"},
                        {
                            "piece": "image",
                            "data": base64.b64encode(A_TINY_PNG).decode("ascii"),
                            "media_type": "image/png",
                        },
                    ],
                    "sender_label": "owner",
                },
            )

            written = harness.backend("c").written_contents
            assert len(written) == 1
            said, picture = written[0]
            assert said == MessageText(text="look at this")
            assert isinstance(picture, MessageImage)
            assert picture.media_type == "image/png"
            # The adapter is handed the file the record named, so what it delivers and
            # what the record holds cannot drift apart.
            assert await harness.message_files.read("c", picture.stored_file_id) == A_TINY_PNG

    _run(exercise)


def test_a_message_with_nothing_in_it_is_refused_rather_than_recorded(
    harness: _Harness,
) -> None:
    """An empty send would put an empty prompt in front of an agent and tell nobody."""

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            empty = await client.post(
                "/api/conversation/conversations/c/send",
                json={"content": [], "sender_label": "owner"},
            )
            assert empty.status_code == 422

            rows = (
                await client.get("/api/conversation/conversations/c/events")
            ).json()["events"]
            assert rows == []

    _run(exercise)


def _compression_as_the_server_composes_it(app: FastAPI) -> CompressExceptEventStreams:
    """The compression the server puts in front of this router, composed the same way.

    The server reads which routes are streams from the routers it includes, so the test
    reads them from the same router under the same prefix.
    """
    return CompressExceptEventStreams(
        app,
        event_stream_patterns=event_stream_route_patterns(router.routes, CONVERSATION_PREFIX),
        minimum_size=1024,
        compresslevel=4,
    )
