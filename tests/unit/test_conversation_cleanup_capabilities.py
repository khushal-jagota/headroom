"""Failed observers and retained sinks cannot escape conversation runtime cleanup."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal

import pytest
from tests.unit.test_conversation_system import _FakeBackend, _FakeBackendChild, _Harness, _start

from planner.conversation.api import ConversationRuntime, build_conversation_runtime
from planner.conversation.backends.contracts import BackendEventSink
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    ResolvedConversationStart,
)
from planner.conversation.events import AgentMessageDeltaFrame, ModelThinkingFrame
from planner.conversation.lifecycle_storage import ConversationRuntimeAlreadyOwned
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.core import server
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema


def _create_database(path: Path) -> None:
    conn = connect(str(path))
    try:
        create_schema(conn)
    finally:
        conn.close()


def test_failed_lifecycle_observer_still_completes_real_lifespan_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "planner.db"
    _create_database(db_path)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
        },
    )
    backends: dict[str, _FakeBackend] = {}
    real_builder = build_conversation_runtime

    def fake_child(
        *,
        resolved_start: ResolvedConversationStart,
        event_sink: BackendEventSink,
        message_files: ConversationMessageFiles,
    ) -> _FakeBackendChild:
        backend = backends.setdefault(
            resolved_start.conversation_id, _FakeBackend(resolved_start.conversation_id)
        )
        return _FakeBackendChild(backend, event_sink)

    def build_isolated_runtime(**kwargs: Any) -> ConversationRuntime:
        kwargs["backend_child_factories"] = {key: fake_child for key in ConversationBackendKey}
        return real_builder(**kwargs)

    async def failed_observer(*args: object, **kwargs: object) -> None:
        raise RuntimeError("synthetic lifecycle observer failure")

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    monkeypatch.setattr(server, "build_conversation_runtime", build_isolated_runtime)
    monkeypatch.setattr(server, "observe_path_changes", failed_observer)
    app = server.create_app(config, build_clock(config), conn_factory)

    async def exercise() -> None:
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        runtime = app.state.conversation
        assert isinstance(runtime, ConversationRuntime)
        await runtime.system.start_conversation(
            ConversationStartRequest(
                conversation_id="live",
                backend_key=ConversationBackendKey.codex,
                model="synthetic-model",
                workspace_folder=tmp_path,
            )
        )
        await runtime.system.send("live", text_message_content("work"), sender_label="owner")
        child = backends["live"]
        child.stop_has_begun = asyncio.Event()
        child.stops_wait_for_release = asyncio.Event()
        writer_started = threading.Event()
        writer_release = threading.Event()
        draining_writes = asyncio.Event()
        original_finish_writes = runtime.store.finish_writes

        def blocked_write() -> None:
            conn = connect(str(db_path))
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE conversations SET model=model WHERE conversation_id='live'")
                writer_started.set()
                if not writer_release.wait(10):
                    raise RuntimeError("synthetic writer release timed out")
                conn.execute("COMMIT")
            finally:
                conn.close()

        async def observe_write_drain() -> None:
            draining_writes.set()
            await original_finish_writes()

        monkeypatch.setattr(runtime.store, "finish_writes", observe_write_drain)
        writer = asyncio.create_task(runtime.store._write(blocked_write))
        assert await asyncio.to_thread(writer_started.wait, 5)

        async def close_lifespan() -> None:
            await context.__aexit__(None, None, None)

        closing = asyncio.create_task(close_lifespan())
        stop_started = asyncio.create_task(child.stop_has_begun.wait())
        try:
            await asyncio.wait(
                (closing, stop_started), timeout=5, return_when=asyncio.FIRST_COMPLETED
            )
            assert child.stop_has_begun.is_set(), "observer failure bypassed runtime shutdown"
            assert runtime.system._shutting_down
            assert not closing.done()
            with pytest.raises(ConversationRuntimeAlreadyOwned):
                ConversationStore(str(db_path)).claim_runtime()
            assert all(
                state.backend_event_pump is None for state in runtime.system._conversations.values()
            )
            assert all(
                not state.settlement_tasks for state in runtime.system._conversations.values()
            )
            child.stops_wait_for_release.set()
            await asyncio.wait_for(draining_writes.wait(), 5)
            assert runtime.store._write_operations
            assert not closing.done()
            with pytest.raises(ConversationRuntimeAlreadyOwned):
                ConversationStore(str(db_path)).claim_runtime()
            writer_release.set()
            await writer
            with pytest.raises(RuntimeError, match="synthetic lifecycle observer failure"):
                await closing
            assert runtime.system._runtime_ownership.closed
            assert not runtime.store._write_operations
            ownership = ConversationStore(str(db_path)).claim_runtime()
            ownership.close()
            assert app.state.conversation is None
        finally:
            writer_release.set()
            await writer
            child.stops_wait_for_release.set()
            stop_started.cancel()
            with suppress(asyncio.CancelledError):
                await stop_started
            with suppress(RuntimeError):
                await closing
            await runtime.shutdown()

    asyncio.run(asyncio.wait_for(exercise(), 15))


@pytest.mark.parametrize("phase", ["during", "after"])
def test_retained_sink_cannot_publish_after_shutdown_starts(
    tmp_path: Path, phase: Literal["during", "after"]
) -> None:
    db_path = tmp_path / "planner.db"
    _create_database(db_path)

    async def exercise() -> None:
        harness = _Harness(db_path)
        await _start(harness, "c")
        await harness.system.send("c", text_message_content("work"), sender_label="owner")
        backend = harness.backend("c")
        sink, token = backend.sink, backend.live_turn_token
        assert sink is not None and token is not None
        subscription = harness.watch("c")
        backend.stop_has_begun = asyncio.Event()
        backend.stops_wait_for_release = asyncio.Event()
        shutdown: asyncio.Task[None] | None = None
        try:
            await sink.agent_message_delta(token, "active")
            await sink.model_thinking_happened(token)
            assert isinstance(await subscription.next_item(), AgentMessageDeltaFrame)
            assert isinstance(await subscription.next_item(), ModelThinkingFrame)
            assert subscription._items.empty()
            shutdown = asyncio.create_task(harness.system.shutdown())
            await asyncio.wait_for(backend.stop_has_begun.wait(), 5)
            if phase == "after":
                backend.stops_wait_for_release.set()
                await shutdown
            harness.clock.advance(100)
            await sink.agent_message_delta(token, "stale")
            await sink.model_thinking_happened(token)
            assert subscription._items.empty(), f"retained sink published {phase} shutdown"
        finally:
            backend.stops_wait_for_release.set()
            if shutdown is not None:
                await shutdown
            else:
                await harness.system.shutdown()
            subscription.close()

    asyncio.run(asyncio.wait_for(exercise(), 15))
