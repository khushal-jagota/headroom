"""The conversation system over HTTP, driven the way a browser drives it.

The subject is the real system on a real database, behind the real router. What stands in
for a vendor is one fake backend adapter, so a test asking "did this text reach the agent"
is answered by the backend side's own account rather than by the system's report of itself.

The endless routes — the tail — are driven straight as an ASGI application, because a
response that never ends cannot be collected first and handed back afterwards.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable, Coroutine, Iterator
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
    PromptWriteFailed,
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
    PermissionAskOption,
    ToolCallStatus,
)
from planner.conversation.live_tail import MAXIMUM_HELD_TAIL_ITEMS, ConversationLiveTail
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
    written_texts: list[str] = field(default_factory=list)
    steered_texts: list[str] = field(default_factory=list)
    permission_answers: dict[str, str] = field(default_factory=dict)
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
        text: str,
        *,
        sender_label: str,
        mode: PromptDeliveryMode,
        model_change: str | None,
        reasoning_effort_change: str | None,
    ) -> None:
        del sender_label, mode, model_change, reasoning_effort_change
        if self._backend.write_fails:
            raise PromptWriteFailed(self._backend.conversation_id)
        self._backend.written_texts.append(text)
        self._backend.live_turn_token = turn_token

    async def steer(self, text: str, *, sender_label: str) -> None:
        del sender_label
        self._backend.steered_texts.append(text)

    async def cancel_running_turn(self) -> None:
        self._backend.cancellations += 1
        self._backend.live_turn_token = None

    async def answer_permission_ask(self, ask_id: str, option_id: str) -> None:
        self._backend.permission_answers[ask_id] = option_id

    async def stop(self) -> None:
        self._backend.live_turn_token = None


# --- the machine, without touching the machine --------------------------------------------


@dataclass
class _FakeMachine:
    """Everything the snapshot is allowed to touch, answered from a script."""

    executables: dict[str, str] = field(default_factory=dict)
    outcomes: dict[tuple[str, ...], CommandOutcome] = field(default_factory=dict)
    run_commands: list[tuple[str, ...]] = field(default_factory=list)

    def executable_path(self, executable_name: str) -> str | None:
        return self.executables.get(executable_name)

    def real_path(self, path: str) -> str:
        return path

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
            command, CommandOutcome(exit_code=-1, standard_output="", standard_error="no such")
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
        self.store = ConversationStore(str(db_path))
        self.live_tail = ConversationLiveTail()
        self.backends: dict[str, _FakeBackend] = {}
        self.machine = _FakeMachine()
        self.system = SqliteProcessConversationSystem(
            store=self.store,
            backend_child_factories={
                backend_key: self._make_child for backend_key in ConversationBackendKey
            },
            live_tail=self.live_tail,
        )
        self.runtime = ConversationRuntime(
            store=self.store,
            system=self.system,
            live_tail=self.live_tail,
            backend_snapshots=BackendSnapshotService(
                self.machine,
                codex_model_catalog_probe=_no_codex_to_ask,
                claude_model_catalog_probe=_claude_from_the_handshake,
            ),
            sse_heartbeat_ms=HEARTBEAT_MILLISECONDS,
        )
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/conversation")
        self.app.state.conversation = self.runtime

    def _make_child(
        self, *, resolved_start: ResolvedConversationStart, event_sink: BackendEventSink
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

        async def send(message: dict[str, Any]) -> None:
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
                json={"text": "first", "sender_label": "owner", "mode": "run_when_free"},
            )
            assert started.status_code == 200
            assert started.json() == {"fate": "started"}

            queued = await client.post(
                "/api/conversation/conversations/c/send",
                json={"text": "held", "sender_label": "owner", "mode": "run_when_free"},
            )
            assert queued.json() == {"fate": "queued", "queue_position": 1}

            injected = await client.post(
                "/api/conversation/conversations/c/send",
                json={"text": "also this", "sender_label": "owner", "mode": "steer"},
            )
            assert injected.json() == {"fate": "injected"}
            assert harness.backend("c").steered_texts == ["also this"]

            refused = await client.post(
                "/api/conversation/conversations/unknown/send",
                json={"text": "nowhere", "sender_label": "owner", "mode": "run_when_free"},
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
                    "text": "first",
                    "sender_label": "owner",
                    "sender_message_id": "m-1",
                    "sent_at_unix_milliseconds": 1_700_000_000_123,
                },
            )
            assert delivered.json() == {"fate": "started"}

            held = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "text": "held",
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
                json={"text": "running", "sender_label": "owner"},
            )
            await client.post(
                "/api/conversation/conversations/k/send",
                json={"text": "never ran", "sender_label": "owner", "sender_message_id": "m-3"},
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
                json={"text": "first", "sender_label": "owner"},
            )

            response = await client.post(
                "/api/conversation/conversations/c/send",
                json={
                    "text": "steered",
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
                json={"text": "work", "sender_label": "owner"},
            )
            await client.post(
                "/api/conversation/conversations/c/send",
                json={"text": "held one", "sender_label": "owner"},
            )
            await client.post(
                "/api/conversation/conversations/c/send",
                json={"text": "held two", "sender_label": "automatic-loop"},
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


def test_an_answer_lands_once_and_then_has_nothing_left_to_land_on(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            await _start(client, "c")
            await client.post(
                "/api/conversation/conversations/c/send",
                json={"text": "work", "sender_label": "owner"},
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
                json={"text": "incumbent", "sender_label": "owner"},
            )
            await client.post(
                "/api/conversation/conversations/interrupted/send",
                json={"text": "held", "sender_label": "owner"},
            )

            stopped = await client.post("/api/conversation/conversations/interrupted/interrupt")
            await harness.settle()

            assert stopped.status_code == 204
            assert harness.backend("interrupted").written_texts == ["incumbent", "held"]

            await _start(client, "killed")
            await client.post(
                "/api/conversation/conversations/killed/send",
                json={"text": "incumbent", "sender_label": "owner"},
            )
            await client.post(
                "/api/conversation/conversations/killed/send",
                json={"text": "held", "sender_label": "owner"},
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
                json={"text": "work", "sender_label": "owner", "mode": "run_when_free"},
            )
            await harness.complete_turn("c")

            everything = (
                await client.get("/api/conversation/conversations/c/events")
            ).json()["events"]
            assert [event["sequence"] for event in everything] == [1, 2]
            assert [event["kind"] for event in everything] == ["prompt", "turn_ended"]
            assert everything[0]["payload"] == {
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
                    "c", AgentMessageEventPayload(text=text)
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
                            conversation_id, AgentMessageEventPayload(text=text)
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
                    "c", AgentMessageEventPayload(text="row five")
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
                json={"text": "work", "sender_label": "owner"},
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
                json={"text": "work", "sender_label": "owner"},
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
                json={"text": "think hard about this", "sender_label": "owner"},
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
                "detail": "`hermes` is not installed or not on PATH.",
                "output_tail": "",
            }

    _run(exercise)


def test_an_unknown_backend_is_not_a_backend(harness: _Harness) -> None:
    async def exercise() -> None:
        async with harness.client() as client:
            assert (
                await client.post("/api/conversation/backends/gemini/update")
            ).status_code == 422

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
            json={"conversation_id": "wired", "backend_key": "codex"},
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
