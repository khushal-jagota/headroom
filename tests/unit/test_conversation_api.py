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
from contextlib import AsyncExitStack
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.executor_turns import persist_pending_turn
from tests.support.model_catalogue import seed_test_model_catalogue
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.api import (
    COMMITTED_EVENT_STREAM_NAME,
    LIVE_FRAME_STREAM_NAME,
    PUBLIC_TOOL_CALL_DETAIL_MAXIMUM_CHARACTERS,
    ConversationRuntime,
    router,
)
from planner.conversation.backend_usage import (
    BackendUsageService,
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
    BackendPromptAccepted,
    BackendSpawnFailed,
    BackendSteerAccepted,
    BackendSteerOutcome,
    BackendUserInputRequest,
    NeedsRebind,
    PromptWriteFailed,
    SessionLoadFailed,
    TurnToken,
)
from planner.conversation.contracts import (
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AgentMessageEventPayload,
    ConversationTurnEnding,
    PermissionAskedEventPayload,
    PermissionAskOption,
    ToolCallFinishedEventPayload,
    ToolCallStatus,
    UserInputAnswer,
    UserInputOption,
    UserInputQuestion,
    UserInputRequestedEventPayload,
)
from planner.conversation.live_tail import MAXIMUM_HELD_TAIL_ITEMS, ConversationLiveTail
from planner.conversation.message_content import (
    MessageContent,
    MessageImage,
    MessageText,
    message_content_text,
    text_message_content,
)
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.runtime_composition import (
    SignalRuntimeComposition,
    bind_admission,
    transaction_control,
)
from planner.conversation.snapshot import (
    BackendSnapshotService,
    CommandOutcome,
)
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import PlannerError, Principal, PrincipalKind
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
        self,
        resolved_start: ResolvedConversationStart,
        *,
        vendor_session_cursor: str | None,
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
        sender_label: str,
        sender_content: MessageContent,
        sender_message_count: int = 1,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
        automatic_compaction: bool = False,
    ) -> BackendPromptAccepted:
        del (
            sender_content,
            sender_message_count,
            sender_label,
            mode,
            model_change,
            reasoning_effort_change,
            automatic_compaction,
        )
        if self._backend.needs_failed_child_recovery_once:
            self._backend.needs_failed_child_recovery_once = False
            raise NeedsRebind(self._backend.conversation_id, failed_child_recovery=True)
        if self._backend.write_fails:
            raise PromptWriteFailed(self._backend.conversation_id)
        self._backend.written_contents.append(content)
        self._backend.live_turn_token = turn_token
        return BackendPromptAccepted(composed_content_delivered=True)

    async def steer(
        self,
        turn_token: TurnToken,
        content: MessageContent,
        *,
        sender_content: MessageContent | None = None,
        sender_label: str,
    ) -> BackendSteerOutcome:
        del turn_token
        del sender_content
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
        models=models,
        reasoning_effort_options=("low", "medium", "high", "xhigh", "max"),
    )


async def _no_codex_to_ask(codex_executable: str) -> CodexModelCatalog:
    """No test here spawns a codex app-server to ask it what it runs."""
    del codex_executable
    raise CodexModelCatalogUnavailable("no codex in a unit test")


# --- the subject ---------------------------------------------------------------------------


class _Harness:
    """The real router over the real system, with a fake backend and a fake machine."""

    def __init__(
        self,
        db_path: Path,
        *,
        signal_composition: SignalRuntimeComposition | None = None,
        cleanup: AsyncExitStack | None = None,
    ) -> None:
        self.db_path = db_path
        self.store = ConversationStore(
            str(db_path),
            signal_coordinator=(
                None if signal_composition is None else signal_composition.coordinator
            ),
            bind_signal_admission=None if signal_composition is None else bind_admission,
            runtime_reconciliation_admission=(
                None if signal_composition is None else signal_composition.initialization
            ),
            transaction_control_guard=None if signal_composition is None else transaction_control,
        )
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
            runtime_ownership=None if signal_composition is None else signal_composition.ownership,
        )
        if cleanup is not None:

            async def shutdown_constructed_runtime() -> None:
                runtime = getattr(self, "runtime", None)
                if runtime is None:
                    await self.system.shutdown()
                else:
                    await runtime.shutdown()

            cleanup.push_async_callback(shutdown_constructed_runtime)
        if signal_composition is not None:
            signal_composition.transfer_ownership()
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
                        option_id="allow-once",
                        label="Approve once",
                        option_kind="allow",
                    ),
                    PermissionAskOption(option_id="deny", label="Decline", option_kind="reject"),
                ),
            ),
        )
        await self.settle()
        return next(
            event.payload.ask_id
            for event in reversed(await self.store.read_events_after(conversation_id, 0))
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
        await backend.sink.user_input_requested(token, request)
        await self.settle()
        public_id = next(
            event.payload.request_id
            for event in reversed(await self.store.read_events_after(conversation_id, 0))
            if isinstance(event.payload, UserInputRequestedEventPayload)
        )
        return replace(request, request_id=public_id)

    async def stream_tool_output(
        self, conversation_id: str, tool_call_id: str, detail: str
    ) -> None:
        backend = self.backend(conversation_id)
        token = backend.live_turn_token
        assert token is not None and backend.sink is not None
        await backend.sink.tool_call_progress(token, tool_call_id=tool_call_id, detail=detail)
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
    seed_test_model_catalogue(conn)
    conn.close()
    built = _Harness(db_path)
    yield built
    asyncio.run(built.runtime.shutdown())


def _run(exercise: Callable[[], Coroutine[Any, Any, None]]) -> None:
    asyncio.run(asyncio.wait_for(exercise(), 20.0))


def _associate_ticket_conversations(harness: _Harness, *, active: str, past: str) -> None:
    conn = connect(str(harness.db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            title="Conversation boundary",
            worker_type="coding",
            principal=OWNER_PRINCIPAL,
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
            return name_line.removeprefix("event: "), json.loads(data_line.removeprefix("data: "))


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
            assert active_send.status_code == 409
            assert active_send.json()["detail"] == (
                "employee conversations accept messages only through Send Message"
            )

            assert (await _start(client, "agent-owned")).status_code == 201
            conn = connect(str(harness.db_path))
            conn.execute(
                "INSERT INTO agents (agent_key, conversation_id) VALUES (?, ?)",
                ("chief_of_staff", "agent-owned"),
            )
            conn.commit()
            conn.close()
            agent_send = await client.post(
                "/api/conversation/conversations/agent-owned/send",
                json={
                    "content": [{"piece": "text", "text": "bypass"}],
                    "sender_label": "owner",
                },
            )
            assert agent_send.status_code == 409
            assert agent_send.json()["detail"] == (
                "employee conversations accept messages only through Send Message"
            )
            conn = connect(str(harness.db_path))
            conn.execute(
                "UPDATE conversations SET identity_environment_variables = ? "
                "WHERE conversation_id = ?",
                ('[["PLAN_ACTOR","chief"]]', "agent-owned"),
            )
            conn.execute(
                "UPDATE agents SET conversation_id = NULL WHERE agent_key = ?",
                ("chief_of_staff",),
            )
            conn.commit()
            conn.close()
            historical_agent_send = await client.post(
                "/api/conversation/conversations/agent-owned/send",
                json={
                    "content": [{"piece": "text", "text": "historical bypass"}],
                    "sender_label": "owner",
                },
            )
            assert historical_agent_send.status_code == 409
            assert historical_agent_send.json()["detail"] == (
                "employee conversations accept messages only through Send Message"
            )

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


# --- sending -----------------------------------------------------------------------------


def test_owner_read_position_persists_and_refuses_employee_updates(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "first"}],
                    "sender_label": "owner",
                },
            )
            advanced = await client.post(
                "/api/conversation/conversations/c/owner-read",
                json={"through_sequence": 999},
            )
            assert advanced.status_code == 200
            assert advanced.json() == {"owner_read_through_sequence": 1}
            assert (await client.get("/api/conversation/conversations/c")).json()[
                "owner_read_through_sequence"
            ] == 1
            async with harness.client() as second_device:
                assert (await second_device.get("/api/conversation/conversations/c")).json()[
                    "owner_read_through_sequence"
                ] == 1

            # A stale browser watermark cannot clear a later addressed message.
            for text in ("earlier owner message", "later owner message"):
                await harness.system.record_message_to_owner(
                    "c",
                    text_message_content(text),
                    sender_label="Worker",
                    sender=Principal(PrincipalKind.ticket, "t_one"),
                    recipient=OWNER_PRINCIPAL,
                )
            events = await harness.store.read_events_after("c", 1)
            earlier, later = events
            stale = await client.post(
                "/api/conversation/conversations/c/owner-read",
                json={"through_sequence": earlier.sequence},
            )
            assert stale.json() == {"owner_read_through_sequence": earlier.sequence}
            assert (await harness.store.attention_facts(["c"]))["c"].unread_message_to_owner
            latest = await client.post(
                "/api/conversation/conversations/c/owner-read",
                json={"through_sequence": later.sequence},
            )
            assert latest.json() == {"owner_read_through_sequence": later.sequence}
            assert not (await harness.store.attention_facts(["c"]))["c"].unread_message_to_owner
            older = await client.post(
                "/api/conversation/conversations/c/owner-read",
                json={"through_sequence": earlier.sequence},
            )
            assert older.json() == {"owner_read_through_sequence": later.sequence}

            missing = await client.post(
                "/api/conversation/conversations/missing/owner-read",
                json={"through_sequence": 1},
            )
            assert missing.status_code == 404

            with pytest.raises(PlannerError, match="only to the owner"):
                await client.post(
                    "/api/conversation/conversations/c/owner-read",
                    json={"through_sequence": 1},
                    headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_one"},
                )

    _run(exercise)


@pytest.mark.parametrize("request_kind", ["permission", "user-input"])
def test_a_stale_http_answer_cannot_target_a_reused_provider_request(
    harness: _Harness, request_kind: str
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")

            async def ask() -> str:
                sent = await client.post(
                    "/api/conversation/conversations/c/send",
                    json={
                        "content": [{"piece": "text", "text": "work"}],
                        "sender_label": "owner",
                    },
                )
                assert sent.status_code == 200, sent.text
                if request_kind == "permission":
                    harness.backend("c").asks_raised = 0
                    return await harness.raise_permission_ask("c")
                return (await harness.request_user_input("c")).request_id

            old_id = await ask()
            await harness.complete_turn("c")
            new_id = await ask()
            assert old_id != new_id

            async def answer(identity: str) -> httpx.Response:
                if request_kind == "permission":
                    body: dict[str, Any] = {"ask_id": identity, "option_id": "deny"}
                else:
                    body = {
                        "request_id": identity,
                        "answers": {
                            "scope": {"answers": ["Backend"]},
                            "timing": {"answers": ["Now"]},
                        },
                    }
                return await client.post(
                    f"/api/conversation/conversations/c/{request_kind}-answers", json=body
                )

            stale = await answer(old_id)
            assert stale.status_code == 200
            assert stale.json() == {"landed": False}
            assert not harness.backend("c").permission_answers
            assert not harness.backend("c").user_input_answers
            current = await answer(new_id)
            assert current.json() == {"landed": True}
            if request_kind == "permission":
                assert harness.backend("c").permission_answers == {"ask-1": "deny"}
            else:
                assert set(harness.backend("c").user_input_answers) == {"input-1"}

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
                question["question_id"] for question in view["pending_user_input"]["questions"]
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
            assert harness.backend("c").user_input_answers["input-1"] == (
                UserInputAnswer("scope", ("Backend", "Frontend")),
                UserInputAnswer("timing", ("Tomorrow",)),
            )
            refreshed = (await client.get("/api/conversation/conversations/c")).json()
            assert refreshed["pending_user_input"] is None

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
            held = (await client.get("/api/conversation/conversations/c")).json()["held_prompts"][0]
            assert held == {
                "held_prompt_id": held["held_prompt_id"],
                "text": "held",
                "sender_label": "owner",
                "sender_message_id": "sender-one",
                "sent_at_unix_milliseconds": 1234,
                "sender": None,
                "recipient": None,
                "queue_reason": "requested",
                "delivery_uncertain": False,
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


@pytest.mark.parametrize("kind", ["permission", "user_input"])
def test_remote_answer_requires_original_reference_and_accepts_first_choice_offline(
    harness: _Harness,
    kind: str,
) -> None:
    from dataclasses import replace

    from planner.core.clock import RealClock
    from planner.executor.codec import encode_value
    from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
    from planner.executor.contracts import AskKind, AskReference, ChildReference, ExecutorIdentity
    from planner.executor.vps import ExecutorEndpoint

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            endpoint = ExecutorEndpoint(str(harness.db_path), RealClock())
            harness.runtime = replace(harness.runtime, executor_endpoint=endpoint)
            harness.app.state.conversation = harness.runtime
            identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "inc", 1)
            endpoint.store.begin_connection(identity, 100)
            ask = AskReference(
                ChildReference("inc", "child", "c"), TurnToken("c", 1), "ask", AskKind(kind)
            )
            public_id = f"1:{kind}:ask"
            persist_pending_turn(
                endpoint.store,
                ask,
                runtime_overrides={
                    "pending_permission_ask_ids": [public_id] if kind == "permission" else [],
                    "permission_provider_ids": {public_id: "ask"} if kind == "permission" else {},
                    "user_input_provider_ids": {public_id: "ask"} if kind == "user_input" else {},
                },
            )
            if kind == "permission":
                endpoint.store.publish_ask(ask, ("allow", "deny"), 100)
                route = "permission-answers"
                body: dict[str, Any] = {"ask_id": "1:permission:ask", "option_id": "allow"}
            else:
                questions = (UserInputQuestion("q", "Question", "Choose", (), False, True),)
                persist_pending_turn(
                    endpoint.store,
                    ask,
                    runtime_overrides={
                        "pending_user_input_questions": {public_id: encode_value(questions)},
                        "permission_provider_ids": {},
                        "user_input_provider_ids": {public_id: "ask"},
                    },
                )
                endpoint.store.publish_user_input_request(ask, questions, 100)
                route = "user-input-answers"
                body = {"request_id": "1:user_input:ask", "answers": {"q": {"answers": ["answer"]}}}
            endpoint.store.mark_lost(identity, 101)
            path = f"/api/conversation/conversations/c/{route}"
            # Neither an ID nor an identity partly copied from another ask grants admission.
            assert (await client.post(path, json=body)).json() == {"landed": False}
            mismatches = (
                replace(ask, ask_id="other"),
                replace(
                    ask,
                    child=replace(ask.child, conversation_id="elsewhere"),
                    turn=TurnToken("elsewhere", 1),
                ),
                replace(
                    ask, kind=AskKind.user_input if kind == "permission" else AskKind.permission
                ),
                replace(ask, child=replace(ask.child, child_id="replacement")),
                replace(ask, turn=TurnToken("c", 2)),
                replace(ask, child=replace(ask.child, executor_incarnation="replacement")),
            )
            for mismatch in mismatches:
                response = await client.post(
                    path, json={**body, "executor_ask": encode_value(mismatch)}
                )
                assert response.status_code == 200, response.text
                assert response.json() == {"landed": False}
            assert endpoint.store.recorded_answer(ask) is None
            malformed = encode_value(ask)
            assert isinstance(malformed, dict)
            malformed["unexpected"] = True
            assert (
                await client.post(path, json={**body, "executor_ask": malformed})
            ).status_code == 422
            malformed = encode_value(ask)
            assert isinstance(malformed, dict) and isinstance(malformed["turn"], dict)
            malformed["turn"]["turn_number"] = "1"
            assert (
                await client.post(path, json={**body, "executor_ask": malformed})
            ).status_code == 422
            exact = {**body, "executor_ask": encode_value(ask)}
            for actor in ("chief", "worker"):
                headers = {"X-Plan-Actor": actor, "X-Plan-Ticket-ID": "test-ticket"}
                with pytest.raises(PlannerError, match="only to the owner"):
                    await client.post(path, json=exact, headers=headers)
            assert endpoint.store.recorded_answer(ask) is None
            response = await client.post(path, json=exact)
            assert response.status_code == 200 and response.json() == {"landed": True}
            answer = endpoint.store.recorded_answer(ask)
            assert answer is not None
            evidence = endpoint.store.ask_evidence(ask)
            assert evidence is not None and evidence.applied_at_mac_ms is None
            endpoint.store.close_child(ask.child, 102)
            assert (await client.post(path, json=exact)).json() == {"landed": True}
            assert endpoint.store.recorded_answer(ask) == answer

    _run(exercise)


@pytest.mark.parametrize("kind", ["permission", "user_input"])
def test_remote_reference_never_falls_through_to_local_callback(
    harness: _Harness,
    kind: str,
) -> None:
    from planner.executor.codec import encode_value
    from planner.executor.contracts import AskKind, AskReference, ChildReference

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
            if kind == "permission":
                ask_id = await harness.raise_permission_ask("c")
                path = "/api/conversation/conversations/c/permission-answers"
                body: dict[str, Any] = {"ask_id": ask_id, "option_id": "allow-once"}
            else:
                request = await harness.request_user_input("c")
                ask_id = request.request_id
                path = "/api/conversation/conversations/c/user-input-answers"
                body = {
                    "request_id": ask_id,
                    "answers": {
                        "where": {"answers": ["Local"]},
                        "why": {"answers": ["because"]},
                    },
                }
            ask = AskReference(
                ChildReference("inc", "child", "c"), TurnToken("c", 1), ask_id, AskKind(kind)
            )
            response = await client.post(path, json={**body, "executor_ask": encode_value(ask)})
            assert response.status_code == 200 and response.json() == {"landed": False}
            assert harness.backend("c").permission_answers == {}
            assert harness.backend("c").user_input_answers == {}

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
                    await client.get(f"/api/conversation/conversations/{conversation_id}/events")
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

            response = await client.get("/api/conversation/conversations/long-output/events")
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
                            AgentMessageEventPayload(content=text_message_content(text)),
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
                    "c",
                    AgentMessageEventPayload(content=text_message_content("row five")),
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


# --- the wiring in the real application ---------------------------------------------------------


def test_the_application_serves_the_conversation_system_and_puts_it_away(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The additive wiring, proved through the real app: built on start, gone on stop."""
    db_path = tmp_path / "wired.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)})

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
    from planner.executor.contracts import (
        AskReachability,
        AskReference,
        ChildInventory,
        ChildReference,
        ExecutorIdentity,
        ReconciliationInventory,
    )
    from planner.executor.store import ExecutorStore

    executor_store = ExecutorStore(str(db_path))
    identity = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "inc", 1)
    executor_store.begin_connection(identity, 100)
    asks = tuple(
        AskReference(ChildReference("inc", name, name), TurnToken(name, 1), "ask")
        for name in ("child-gone", "callback-gone", "live")
    )
    for ask in asks:
        executor_store.publish_ask(ask, ("allow", "deny"), 100)
    executor_store.reconcile(
        ReconciliationInventory(
            identity,
            (
                ChildInventory(asks[1].child, asks[1].turn, (), ()),
                ChildInventory(asks[2].child, asks[2].turn, (asks[2],), ()),
            ),
            (),
            (),
        ),
        101,
    )
    app = create_app(config, build_clock(config), conn_factory)
    with TestClient(app) as client:
        # This wiring proof creates a row without any provider catalogue probe.
        monkeypatch.setattr(app.state.conversation.system, "_model_resolver", None)
        # Startup discards live reachability but retains conclusive callback closure.
        for ask, expected in zip(
            asks,
            (
                AskReachability.child_gone,
                AskReachability.callback_gone,
                AskReachability.uncertain,
            ),
            strict=True,
        ):
            evidence = executor_store.ask_evidence(ask)
            assert evidence is not None and evidence.reachability == expected
        assert client.get("/api/conversation/conversations/nobody").status_code == 404

        created = client.post(
            "/api/conversation/conversations",
            json={
                "conversation_id": "wired",
                "model": "a-model",
                "backend_key": "codex",
            },
        )
        assert created.status_code == 201
        assert created.json()["backend_key"] == "codex"

        view = client.get("/api/conversation/conversations/wired")
        assert view.status_code == 200
        # Creating a conversation spawns nothing, which is why this test can run against
        # the real backends without an agent starting anywhere.
        assert view.json()["is_running"] is False

        events = client.get("/api/conversation/conversations/wired/events")
        assert events.json() == {"events": [], "has_more": False, "diagnostics_expired": False}

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


def test_the_picture_reaches_the_backend_and_not_just_the_record(
    harness: _Harness,
) -> None:
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


def test_legacy_sparse_events_report_real_edges_and_expired_details(harness: _Harness) -> None:
    """Omissions are disclosed, and neither sequence gaps nor unknown details invent history."""
    from planner.core.db import connect
    from planner.core.server import handle_planner_error

    harness.app.add_exception_handler(PlannerError, handle_planner_error)

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "sparse")
            for text in ("first", "expired", "last"):
                await harness.store.append_event(
                    "sparse", AgentMessageEventPayload(content=text_message_content(text))
                )
            path = f"{CONVERSATION_PREFIX}/conversations/sparse"
            before = await client.get(path + "/events", params={"limit": 1})
            assert before.json()["diagnostics_expired"] is False
            with connect(harness.runtime.database_path) as conn:
                conn.execute(
                    "DELETE FROM conversation_events WHERE conversation_id='sparse' "
                    "AND sequence IN (1,2)"
                )
                conn.execute("INSERT INTO conversation_expired_ranges VALUES ('sparse',1,2,1,2,2)")
            page = (await client.get(path + "/events", params={"limit": 1})).json()
            assert [event["sequence"] for event in page["events"]] == [3]
            assert page["has_more"] is False
            assert page["diagnostics_expired"] is True
            empty = (await client.get(path + "/events", params={"before": 3, "limit": 1})).json()
            assert empty["events"] == [] and empty["has_more"] is False
            forward = (await client.get(path + "/events", params={"after": 0, "limit": 1})).json()
            assert forward["events"] == page["events"] and forward["has_more"] is False
            expired = await client.get(path + "/events/2/detail")
            assert expired.status_code == 410
            assert expired.json()["error"]["code"] == "diagnostic_expired"
            for sequence in (3, 999):
                assert (await client.get(path + f"/events/{sequence}/detail")).status_code == 404
            async with _EventStreamDrive(harness.app, path + "/tail", "after=2") as stream:
                name, event = await stream.next_named_frame()
                assert name == COMMITTED_EVENT_STREAM_NAME and event["sequence"] == 3

    _run(exercise)


def test_suppressed_fate_keeps_its_reason_at_the_http_boundary() -> None:
    from planner.conversation.api import delivery_fate_json
    from planner.conversation.contracts import PromptDeliverySuppressed

    assert delivery_fate_json(PromptDeliverySuppressed("proposal_revision_changed")) == {
        "fate": "suppressed",
        "reason": "proposal_revision_changed",
    }
