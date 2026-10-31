"""Failed observers and retained sinks cannot escape conversation runtime cleanup."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import closing as closing_connection
from contextlib import suppress
from pathlib import Path
from typing import Any, Literal, cast

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
from planner.conversation.lifecycle_storage import (
    ConversationRuntimeAlreadyOwned,
    acquire_runtime_ownership,
)
from planner.conversation.message_content import text_message_content
from planner.conversation.message_files import ConversationMessageFiles
from planner.conversation.storage import ConversationStore
from planner.core import server
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.measure_sources.contracts import SourceRuntime
from planner.runtime.workspaces import register_workspace_root, workspace_root_for_connection
from planner.scheduled_tickets.registry import build_participants
from planner.server_lifecycle import runtime as application_runtime


def _create_database(path: Path) -> None:
    conn = connect(str(path))
    try:
        create_schema(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("first_failure", ["observer", "loop", "body", "recovery"])
def test_failed_lifecycle_observer_still_completes_real_lifespan_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_failure: Literal["observer", "loop", "body", "recovery"],
) -> None:
    db_path = tmp_path / "planner.db"
    _create_database(db_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_WORKSPACE_ROOT": str(workspace),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
            "PLAN_SERVER_CONTROL_SOCKET": str(tmp_path / "control.sock"),
        },
    )
    backends: dict[str, _FakeBackend] = {}
    real_builder = build_conversation_runtime
    observer_error = RuntimeError("synthetic lifecycle observer failure")
    loop_error = RuntimeError("synthetic scheduler shutdown failure")
    body_error = RuntimeError("synthetic lifespan body failure")
    recovery_error = RuntimeError("synthetic recovery failure")
    invalidation_error = RuntimeError("synthetic shutdown invalidation failure")
    stopped_loops: list[object] = []
    real_stop = server._stop_runtime_with_deadline

    async def stop_then_fail(owned: Any, deadline: float) -> None:
        await real_stop(owned, deadline)
        stopped_loops.append(owned)
        raise loop_error

    async def failed_recovery(*args: object, **kwargs: object) -> None:
        raise recovery_error

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
        runtime = real_builder(**kwargs)
        if first_failure == "body":
            coordinator = kwargs["signal_composition"].coordinator
            real_invalidate = coordinator.invalidate

            def invalidate_then_fail(reason: str = "publication_failed") -> None:
                real_invalidate(reason)
                if reason == "shutdown":
                    # Fail this application boundary once. The conversation's
                    # independent shutdown also invalidates the same producer.
                    monkeypatch.setattr(coordinator, "invalidate", real_invalidate)
                    raise invalidation_error

            monkeypatch.setattr(coordinator, "invalidate", invalidate_then_fail)
        # The cleanup fixture uses synthetic models and controlled children, as the
        # ordinary conversation harness does. Catalogue resolution is a separate seam.
        runtime.system._model_resolver = None
        return runtime

    async def failed_observer(*args: object, **kwargs: object) -> None:
        raise observer_error

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    monkeypatch.setattr(application_runtime, "build_conversation_runtime", build_isolated_runtime)
    monkeypatch.setattr(server, "observe_path_changes", failed_observer)
    if first_failure != "observer":
        monkeypatch.setattr(server, "_stop_runtime_with_deadline", stop_then_fail)
    if first_failure == "recovery":
        from planner.conversation.deploy_interruptions import coordinator as recovery

        monkeypatch.setattr(recovery, "run_automatic_recovery", failed_recovery)
    app = server.create_app(
        config,
        build_clock(config),
        conn_factory,
        # No sources or Ticket schedules exist. The shared participants exercise
        # real cleanup without any employee, script, or external request.
        slot_participants_for_test=(
            None
            if first_failure == "observer"
            else lambda admission: build_participants(
                config,
                build_clock(config),
                workspace_admission=admission,
                source_runtime=cast(SourceRuntime, object()),
            )
        ),
    )

    async def exercise() -> None:
        context = app.router.lifespan_context(app)
        await context.__aenter__()
        runtime = app.state.conversation
        assert isinstance(runtime, ConversationRuntime)
        with closing_connection(conn_factory()) as conn:
            assert workspace_root_for_connection(conn) == workspace
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
            with runtime.store._publication_connection() as (conn, _admission):
                conn.execute("BEGIN IMMEDIATE")
                conn.execute("UPDATE conversations SET model=model WHERE conversation_id='live'")
                runtime.store._record_signal_result(conn, None)
                conn.execute("COMMIT")
            # Hold the tracked operation after canonical SQL and its producer gate
            # close. Holding the gate until child stop would deadlock shutdown's
            # preceding invalidation. This proves operation drain, not active SQL.
            writer_started.set()
            if not writer_release.wait(10):
                raise RuntimeError("synthetic writer release timed out")

        async def observe_write_drain() -> None:
            draining_writes.set()
            await original_finish_writes()

        monkeypatch.setattr(runtime.store, "finish_writes", observe_write_drain)
        writer = asyncio.create_task(runtime.store._write(blocked_write))
        assert await asyncio.to_thread(writer_started.wait, 5)

        async def close_lifespan() -> None:
            if first_failure == "body":
                # Match async-with propagation of the original body exception.
                assert not await context.__aexit__(RuntimeError, body_error, None)
                raise body_error
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
            expected = {
                "observer": observer_error,
                "loop": loop_error,
                "body": body_error,
                "recovery": recovery_error,
            }[first_failure]
            with pytest.raises(RuntimeError) as caught:
                await closing
            assert caught.value is expected
            if first_failure != "observer":
                assert len(stopped_loops) == 1
                assert any("observer failure" in note for note in expected.__notes__)
            if first_failure == "body":
                assert any("invalidation failure" in note for note in expected.__notes__)
                assert any("scheduler shutdown failure" in note for note in expected.__notes__)
            assert runtime.system._runtime_ownership.closed
            assert not runtime.store._write_operations
            ownership = acquire_runtime_ownership(str(db_path))
            ownership.close()
            assert app.state.conversation is None
            with register_workspace_root(str(db_path), None):
                pass
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


def test_early_runtime_failure_outlives_registered_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructor-phase cleanup cannot replace the failure that caused shutdown."""
    startup_error = RuntimeError("synthetic bootstrap failure")
    events: list[str] = []

    class FailedComposition:
        def __init__(self, *_args: object) -> None:
            pass

        def bootstrap(self) -> None:
            raise startup_error

        def close(self) -> None:
            events.append("close")
            raise RuntimeError("synthetic composition cleanup failure")

    async def unused_shutdown() -> None:
        raise AssertionError("no dependent was constructed")

    monkeypatch.setattr(application_runtime, "SignalRuntimeComposition", FailedComposition)

    async def exercise() -> None:
        with pytest.raises(RuntimeError) as caught:
            async with application_runtime.application_runtime(
                db_path="unused",
                db_busy_timeout_ms=1000,
                sse_heartbeat_ms=1000,
                backend_child_factories={},
                remembered_codex_login_rejection=None,
                navigation_delivery=cast(Any, object()),
                shutdown_dependents=unused_shutdown,
            ):
                raise AssertionError("bootstrap did not fail")
        assert caught.value is startup_error
        assert "synthetic composition cleanup failure" in startup_error.__notes__[0]
        assert events == ["close"]

    asyncio.run(exercise())


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
