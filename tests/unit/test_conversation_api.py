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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.api import (
    COMMITTED_EVENT_STREAM_NAME,
    LIVE_FRAME_STREAM_NAME,
    ConversationRuntime,
    router,
)
from planner.conversation.backend_usage import (
    BackendUsageOutcome,
    BackendUsageResult,
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
    BackendSpawnFailed,
    BackendUserInputRequest,
    PromptWriteFailed,
    TurnToken,
)
from planner.conversation.contracts import (
    AgentCommand,
    ConversationBackendKey,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.events import (
    AgentMessageDeltaFrame,
    AgentMessageEventPayload,
    ConversationTurnEnding,
    PermissionAskOption,
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
    BackendSnapshotService,
    CommandOutcome,
)
from planner.conversation.storage import ConversationStore
from planner.conversation.system import SqliteProcessConversationSystem
from planner.core import sse
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

VENDOR_SESSION_CURSOR = "vendor-session-1"
HEARTBEAT_MILLISECONDS = 40


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
    permission_answers: dict[str, str] = field(default_factory=dict)
    user_input_answers: dict[str, tuple[UserInputAnswer, ...]] = field(default_factory=dict)
    cancellations: int = 0
    live_turn_token: TurnToken | None = None
    sink: BackendEventSink | None = None
    asks_raised: int = 0
    spawn_fails: bool = False
    write_fails: bool = False


class _FakeBackendChild:
    def __init__(self, backend: _FakeBackend, event_sink: BackendEventSink) -> None:
        self._backend = backend
        self._sink = event_sink

    async def start(
        self, resolved_start: ResolvedConversationStart, *, vendor_session_cursor: str | None
    ) -> None:
        del resolved_start
        self._backend.sink = self._sink
        if vendor_session_cursor is None:
            await self._sink.vendor_session_cursor_rebound(VENDOR_SESSION_CURSOR)

    async def write_prompt(
        self,
        turn_token: TurnToken,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
    ) -> None:
        del sender_label, mode, model_change, reasoning_effort_change
        if self._backend.write_fails:
            raise PromptWriteFailed(self._backend.conversation_id)
        self._backend.written_contents.append(content)
        self._backend.live_turn_token = turn_token

    async def steer(self, content: MessageContent, *, sender_label: str) -> None:
        del sender_label
        self._backend.steered_contents.append(content)

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
            backend_snapshots=BackendSnapshotService(
                self.machine,
                codex_model_catalog_probe=_no_codex_to_ask,
                claude_model_catalog_probe=_claude_from_the_handshake,
            ),
            backend_usage=BackendUsageService({}),
            sse_heartbeat_ms=HEARTBEAT_MILLISECONDS,
        )
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/conversation")
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

    def __init__(self, app: FastAPI, path: str, query: str) -> None:
        self._app = app
        self._path = path
        self._query = query
        self._chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._buffer = ""
        self._task: asyncio.Task[None] | None = None

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
            "headers": [],
            "client": ("127.0.0.1", 51234),
            "server": ("127.0.0.1", 8767),
            "app": self._app,
        }

        async def receive() -> dict[str, Any]:
            # The browser is still there: nothing is ever sent up this stream.
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        async def send(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.body":
                await self._chunks.put(bytes(message.get("body", b"")))

        self._task = asyncio.create_task(self._app(scope, receive, send))
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


def test_starting_a_conversation_answers_with_what_it_resolved_to(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            response = await _start(client, "c", model="a-model", reasoning_effort="high")

            assert response.status_code == 201
            view = response.json()
            assert view["conversation_id"] == "c"
            assert view["backend_key"] == "hermes"
            assert view["model"] == "a-model"
            assert view["reasoning_effort"] == "high"
            assert view["workspace_folder"] == "/tmp/workspace"
            # The floor default, applied because the request said nothing about access.
            assert view["access"] == "full"
            assert view["is_running"] is False
            assert view["latest_sequence"] == 0
            assert view["held_prompt_count"] == 0
            assert view["pending_permission_ask"] is None
            # Nothing has reported a menu, so there is none to offer.
            assert view["available_commands"] == []

    _run(exercise)


def test_the_view_carries_the_commands_the_agent_says_may_be_typed_at_it(
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
                    "mode": "run_when_free",
                },
            )
            backend = harness.backend("c")
            assert backend.sink is not None
            await backend.sink.available_commands_reported(
                (
                    AgentCommand(
                        name="review", description="Review the diff", argument_hint="[path]"
                    ),
                    AgentCommand(name="compact", description="Summarise the conversation so far"),
                )
            )
            await harness.settle()

            view = (await client.get("/api/conversation/conversations/c")).json()
            assert view["available_commands"] == [
                {
                    "name": "review",
                    "description": "Review the diff",
                    "argument_hint": "[path]",
                },
                {
                    "name": "compact",
                    "description": "Summarise the conversation so far",
                    "argument_hint": None,
                },
            ]

    _run(exercise)


def test_starting_the_same_conversation_twice_is_a_conflict(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            assert (await _start(client, "c")).status_code == 201
            assert (await _start(client, "c")).status_code == 409

    _run(exercise)


def test_a_request_that_cannot_be_resolved_is_refused(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            # An identity belongs to a role, so there is nowhere for these to go.
            orphaned_identity = await _start(
                client, "c", identity_environment_variables={"PANELS_ROLE": "chief"}
            )
            assert orphaned_identity.status_code == 422

            unknown_backend = await _start(client, "d", backend_key="not-a-backend")
            assert unknown_backend.status_code == 422

    _run(exercise)


def test_a_conversation_keeps_the_names_of_its_identity_and_not_the_values(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(
                client,
                "c",
                role_text="You are the Chief of Staff.",
                identity_environment_variables={"PANELS_ROLE": "chief"},
            )

            view = (await client.get("/api/conversation/conversations/c")).json()
            assert view["role_text"] == "You are the Chief of Staff."
            assert view["identity_environment_variable_names"] == ["PANELS_ROLE"]
            assert "chief" not in json.dumps(view)

    _run(exercise)


def test_a_folder_written_the_way_a_person_writes_it_is_the_folder_they_meant(
    harness: _Harness,
) -> None:
    """``~/Coding`` is what somebody types. Only a shell knows what it means, so the
    boundary that turns typed text into a path is where it has to be worked out."""

    async def exercise() -> None:
        async with harness.client() as client:
            created = await client.post(
                "/api/conversation/conversations",
                json={
                    "conversation_id": "typed",
                    "model": "a-model",
                    "backend_key": "hermes",
                    "workspace_folder": "~/Coding",
                },
            )

            assert created.status_code == 201
            folder = created.json()["workspace_folder"]
            assert folder == str(Path.home() / "Coding")
            assert "~" not in folder

            # And it is the folder the conversation is actually stored as running in.
            stored = await harness.store.read_conversation("typed")
            assert stored is not None
            assert stored.workspace_folder == Path.home() / "Coding"

    _run(exercise)


def test_a_folder_that_is_neither_absolute_nor_a_home_path_is_refused(
    harness: _Harness,
) -> None:
    """Resolving it against wherever the server was started would be a guess."""

    async def exercise() -> None:
        async with harness.client() as client:
            refused = await client.post(
                "/api/conversation/conversations",
                json={
                    "conversation_id": "relative",
                    "backend_key": "hermes",
                    "workspace_folder": "some/relative/folder",
                },
            )

            assert refused.status_code == 422

    _run(exercise)


def test_reading_a_conversation_that_was_never_started_is_a_404(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            assert (
                await client.get("/api/conversation/conversations/never-started")
            ).status_code == 404
            assert (
                await client.get("/api/conversation/conversations/never-started/events")
            ).status_code == 404
            assert (
                await client.get("/api/conversation/conversations/never-started/tail")
            ).status_code == 404

    _run(exercise)


# --- sending -----------------------------------------------------------------------------


def test_every_fate_a_send_can_have_comes_back_tagged(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")

            started = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "first"}],
                    "sender_label": "owner",
                    "mode": "run_when_free",
                },
            )
            assert started.status_code == 200
            assert started.json() == {"fate": "started"}

            queued = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held"}],
                    "sender_label": "owner",
                    "mode": "run_when_free",
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

            refused = await client.post(
                "/api/conversation/conversations/unknown/send",
                json={
                    "content": [{"piece": "text", "text": "nowhere"}],
                    "sender_label": "owner",
                    "mode": "run_when_free",
                },
            )
            assert refused.status_code == 200
            assert refused.json() == {
                "fate": "refused",
                "refusal_reason": "no_such_conversation",
            }

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
                "mode": "run_when_free",
                "sender_message_id": "m-1",
                "sent_at_unix_milliseconds": 1_700_000_000_123,
            }
            assert payloads["prompt_delivery_refused"] == {
                "text": "held",
                "sender_label": "owner",
                "mode": "run_when_free",
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


def test_a_steer_carrying_a_change_is_a_caller_error(harness: _Harness) -> None:
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

            response = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "steered"}],
                    "sender_label": "owner",
                    "mode": "steer",
                    "model_change": "another-model",
                },
            )

            assert response.status_code == 422

    _run(exercise)


def test_the_view_says_what_is_running_and_what_is_waiting(harness: _Harness) -> None:
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
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held one"}],
                    "sender_label": "owner",
                },
            )
            await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [{"piece": "text", "text": "held two"}],
                    "sender_label": "automatic-loop",
                },
            )
            ask_id = await harness.raise_permission_ask("c")

            view = (await client.get("/api/conversation/conversations/c")).json()

            assert view["is_running"] is True
            assert view["held_prompt_count"] == 2
            assert view["latest_sequence"] == 2
            waiting = view["pending_permission_ask"]
            assert waiting["ask_id"] == ask_id
            assert waiting["title"] == "Run a command?"
            assert waiting["detail"] == "ls -la"
            assert [option["option_id"] for option in waiting["options"]] == [
                "allow-once",
                "deny",
            ]
            assert [option["option_kind"] for option in waiting["options"]] == [
                "allow",
                "reject",
            ]

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
            assert (await client.get("/api/conversation/conversations/c")).json()[
                "held_prompt_count"
            ] == 1

            discarded = await client.delete(
                "/api/conversation/conversations/c/held-prompts/message-one"
            )
            assert discarded.status_code == 200
            assert discarded.json() == {"discarded": True}
            assert (await client.get("/api/conversation/conversations/c")).json()[
                "held_prompt_count"
            ] == 0
            # It reached no backend on the way in, so it reaches none on the way out.
            assert harness.backend("c").written_texts == ["incumbent"]

            # Asking again finds nothing, which is an answer rather than an error.
            again = await client.delete(
                "/api/conversation/conversations/c/held-prompts/message-one"
            )
            assert again.status_code == 200
            assert again.json() == {"discarded": False}

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


def test_interrupting_or_killing_a_conversation_that_is_not_there_changes_nothing(
    harness: _Harness,
) -> None:
    """The contract says both are no-ops on an id that names nothing, so they answer so."""

    async def exercise() -> None:
        async with harness.client() as client:
            assert (
                await client.post("/api/conversation/conversations/nobody/interrupt")
            ).status_code == 204
            assert (
                await client.post("/api/conversation/conversations/nobody/kill")
            ).status_code == 204

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
                    "mode": "run_when_free",
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
                "mode": "run_when_free",
            }
            assert everything[1]["payload"] == {"ending": "completed", "error_summary": None}

            after_the_first = (
                await client.get("/api/conversation/conversations/c/events", params={"after": 1})
            ).json()["events"]
            assert [event["sequence"] for event in after_the_first] == [2]

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


def test_the_backend_cards_are_probed_once_and_again_when_asked(harness: _Harness) -> None:
    async def exercise() -> None:
        harness.machine.executables["hermes"] = "/usr/local/bin/hermes"
        harness.machine.outcomes[("/usr/local/bin/hermes", "--version")] = CommandOutcome(
            exit_code=0,
            standard_output="Hermes Agent v0.18.2\n",
            standard_error="",
        )
        harness.machine.answers_any_other_command = CommandOutcome(
            exit_code=0,
            standard_output=json.dumps(
                {
                    "schemaVersion": 1,
                    "status": "runnable",
                    "defaultModelId": "openai-codex:gpt-5.6-sol",
                    "providers": [
                        {
                            "id": "openai-codex",
                            "displayName": "OpenAI Codex",
                            "models": [
                                {
                                    "id": "openai-codex:gpt-5.6-sol",
                                    "displayName": "GPT-5.6 Sol",
                                    "detail": "OpenAI Codex",
                                }
                            ],
                        }
                    ],
                }
            ),
            standard_error="",
        )
        harness.machine.executables["claude"] = "/usr/local/bin/claude"
        harness.machine.outcomes[("/usr/local/bin/claude", "--version")] = CommandOutcome(
            exit_code=0, standard_output="2.1.219 (Claude Code)\n", standard_error=""
        )
        harness.machine.outcomes[
            ("/usr/local/bin/claude", "auth", "status", "--json")
        ] = CommandOutcome(
            exit_code=0,
            standard_output=json.dumps(
                {
                    "loggedIn": True,
                    "email": "owner@example.com",
                    "authMethod": "claude.ai",
                    "subscriptionType": "max",
                }
            ),
            standard_error="",
        )

        async with harness.client() as client:
            first = await client.get("/api/conversation/backends")
            assert first.status_code == 200
            cards = {card["backend_key"]: card for card in first.json()["backends"]}
            assert set(cards) == {"hermes", "codex", "claude"}

            hermes = cards["hermes"]
            assert hermes["default_model_id"] == "openai-codex:gpt-5.6-sol"
            assert [model["model_id"] for model in hermes["available_models"]] == [
                "openai-codex:gpt-5.6-sol"
            ]
            assert hermes["reasoning_effort_options"] == []

            claude = cards["claude"]
            assert claude["installed"] is True
            assert claude["version"] == "2.1.219"
            assert claude["identity"]["status"] == "authenticated"
            assert claude["identity"]["account_label"] == "owner@example.com"
            assert claude["identity"]["login_command"] == "claude auth login"
            assert [model["model_id"] for model in claude["available_models"]] == [
                "opus[1m]",
                "sonnet",
            ]
            assert claude["available_models"][0]["detail"] == "opus[1m] → claude-opus-5[1m]"
            assert claude["available_models"][0]["display_name"] == "Opus 5 (1M)"

            assert claude["reasoning_effort_options"] == [
                "low",
                "medium",
                "high",
                "xhigh",
                "max",
            ]

            # Not on this machine: said plainly, with nothing offered.
            codex = cards["codex"]
            assert codex["installed"] is False
            assert codex["diagnoses"] == ["`codex` is not installed or not on PATH."]
            assert codex["update_advisory"] is None

            probes_after_the_first_read = len(harness.machine.run_commands)
            await client.get("/api/conversation/backends")
            assert len(harness.machine.run_commands) == probes_after_the_first_read

            await client.get("/api/conversation/backends", params={"refresh": "true"})
            assert len(harness.machine.run_commands) > probes_after_the_first_read

    _run(exercise)


def test_an_update_that_cannot_be_run_says_so_rather_than_pretending(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            response = await client.post("/api/conversation/backends/hermes/update")

            assert response.status_code == 200
            assert response.json() == {
                "outcome": "failed",
                "detail": (
                    "Hermes is not installed at the configured "
                    "PLAN_HERMES_PYTHON environment."
                ),
                "output_tail": "",
            }

    _run(exercise)


def test_the_existing_update_route_exposes_a_native_hermes_result(
    harness: _Harness,
) -> None:
    async def exercise() -> None:
        hermes = "/usr/local/bin/hermes"
        harness.machine.executables["hermes"] = hermes
        harness.machine.outcomes[(hermes, "--version")] = CommandOutcome(
            exit_code=0, standard_output="Hermes Agent v0.18.2\n", standard_error=""
        )
        harness.machine.outcomes[(hermes, "update", "--check")] = CommandOutcome(
            exit_code=0, standard_output="✓ Already up to date.\n", standard_error=""
        )
        harness.machine.outcomes[(hermes, "update", "--yes")] = CommandOutcome(
            exit_code=0, standard_output="✓ Update complete!\n", standard_error=""
        )

        async with harness.client() as client:
            response = await client.post("/api/conversation/backends/hermes/update")

        assert response.status_code == 200
        assert response.json() == {
            "outcome": "unchanged",
            "detail": (
                "The update command finished, but the installed version is still 0.18.2."
            ),
            "output_tail": "✓ Update complete!\n",
        }

    _run(exercise)


def test_an_unknown_backend_is_not_a_backend(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            assert (
                await client.post("/api/conversation/backends/gemini/update")
            ).status_code == 422

    _run(exercise)


def test_usage_refresh_is_explicit_and_has_one_stable_wire_shape(harness: _Harness) -> None:
    class CountingUsage:
        calls = 0

        async def refresh(self) -> BackendUsageResult:
            self.calls += 1
            return BackendUsageResult(
                ConversationBackendKey.codex, BackendUsageOutcome.succeeded
            )

    async def exercise() -> None:
        usage = CountingUsage()
        object.__setattr__(
            harness.runtime,
            "backend_usage",
            BackendUsageService({ConversationBackendKey.codex: usage}),
        )
        async with harness.client() as client:
            ordinary_read = await client.get("/api/conversation/backends")
            response = await client.post(
                "/api/conversation/backends/codex/usage-refresh"
            )

        assert ordinary_read.status_code == 200
        assert usage.calls == 1
        assert response.status_code == 200
        assert response.json() == {
            "backend_key": "codex",
            "outcome": "succeeded",
            "detail": None,
            "observed_at": None,
            "windows": [],
        }

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
        # Backend cards and child startup share the same lifecycle arbiter. This is what
        # makes the update route's check atomic with a real conversation spawn.
        assert (
            app.state.conversation.system._backend_lifecycle
            is app.state.conversation.backend_snapshots._backend_lifecycle
        )

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


@pytest.mark.parametrize(
    ("payload", "claimed_media_type"),
    [
        (MALFORMED_CLAIMED_PNG, "image/png"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
        (b"\x00\x00\x00\x18ftypheicunsupported", "image/heic"),
    ],
)
def test_invalid_or_unsupported_picture_bytes_leave_no_row_or_managed_file(
    harness: _Harness, payload: bytes, claimed_media_type: str
) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            rejected = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [
                        {"piece": "text", "text": "do not record this"},
                        {
                            "piece": "image",
                            "data": base64.b64encode(A_TINY_PNG).decode("ascii"),
                            "media_type": "image/png",
                        },
                        {
                            "piece": "image",
                            "data": base64.b64encode(payload).decode("ascii"),
                            "media_type": claimed_media_type,
                        },
                    ],
                    "sender_label": "owner",
                },
            )
            assert rejected.status_code == 422
            assert "unsupported or invalid" in rejected.json()["detail"]
            assert (
                await client.get("/api/conversation/conversations/c/events")
            ).json() == {"events": []}
            files = harness.db_path.parent / "files" / "conversations"
            assert not list(files.glob("**/*"))

    _run(exercise)


def test_an_oversize_picture_leaves_no_row_or_managed_file(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            # This is rejected from its encoded length before the server allocates a
            # second decoded copy.
            limit = MAX_CONVERSATION_MESSAGE_IMAGE_BYTES
            too_large = "A" * (4 * ((limit + 2) // 3) + 1)
            rejected = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "content": [
                        {
                            "piece": "image",
                            "data": too_large,
                            "media_type": "image/png",
                        }
                    ],
                    "sender_label": "owner",
                },
            )
            assert rejected.status_code == 422
            assert rejected.json()["detail"] == "a conversation image is too large"
            assert (
                await client.get("/api/conversation/conversations/c/events")
            ).json() == {"events": []}
            files = harness.db_path.parent / "files" / "conversations"
            assert not list(files.glob("**/*"))

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


def test_a_file_that_was_never_kept_is_not_there(harness: _Harness) -> None:
    """An id nobody kept anything under is a plain not-found, not an empty answer."""

    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            missing = await client.get(
                "/api/conversation/conversations/c/files/f_never_written"
            )
            assert missing.status_code == 404

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
