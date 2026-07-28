"""What one process owns in the background, and what it gives back when it stops."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from planner.conversation.contracts import ConversationSystem
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core import change_signal, loops
from planner.core.clock import Clock, TestClock
from planner.core.config import Config, load_config
from planner.core.db import connect, create_schema
from planner.worker_context.contracts import WorkerContextService
from planner.worker_context.service import EmptyWorkerContextService


def _config(tmp_path: Path, *, dispatch: bool = True) -> Config:
    db_path = tmp_path / "planning.db"
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    return load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(db_path),
            "PLAN_DISPATCH_ENABLED": "1" if dispatch else "0",
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )


def _start(config: Any, clock: TestClock) -> loops.BackgroundLoops:
    return loops.start_background_loops(
        config,
        clock,
        conversation_system=cast(ConversationSystem, InMemoryConversationSystem()),
        worker_context_service=cast(WorkerContextService, EmptyWorkerContextService()),
        asyncio_loop=asyncio.new_event_loop(),
    )


def test_dispatch_disabled_owns_no_loop_and_takes_no_lock(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, dispatch=False)

    def fail_if_called(path: str) -> bool:
        raise AssertionError(f"a disabled readiness loop must not acquire {path}")

    monkeypatch.setattr(loops, "ensure_machine_lock", fail_if_called)
    subscribers_before = change_signal.subscriber_count()
    handle = _start(config, fake_clock)
    try:
        assert handle.worker_step_readiness_loop is None
        assert handle.scheduled_ticket_loop is None
        assert change_signal.subscriber_count() == subscribers_before
    finally:
        asyncio.run(handle.stop())


def test_polling_lock_loser_owns_no_loop_and_does_not_release_a_foreign_lock(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: False)
    monkeypatch.setattr(
        loops,
        "release_machine_lock",
        lambda path: (_ for _ in ()).throw(AssertionError(path)),
    )
    subscribers_before = change_signal.subscriber_count()
    handle = _start(config, fake_clock)
    try:
        assert handle.worker_step_readiness_loop is None
        assert handle.scheduled_ticket_loop is None
        assert change_signal.subscriber_count() == subscribers_before
    finally:
        asyncio.run(handle.stop())


def test_lock_winner_composes_the_loop_and_wakes_it_from_the_change_signal(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    released: list[str] = []

    class RecordingReadinessLoop:
        def __init__(self, _db_path: str, _clock: Clock, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.started: list[int] = []
            self.wakes = 0
            self.stops: list[float | None] = []
            constructed.append(self)

        def wake(self) -> None:
            self.wakes += 1

        def start(self, interval: int) -> None:
            self.started.append(interval)

        def stop(self, *, deadline: float | None = None) -> None:
            self.stops.append(deadline)

    constructed: list[RecordingReadinessLoop] = []

    class RecordingScheduledLoop:
        def __init__(self, _db_path: str, _clock: Clock, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.started: list[int] = []
            self.stops: list[float | None] = []
            constructed_schedules.append(self)

        def start(self, interval: int) -> None:
            self.started.append(interval)

        def stop(self, *, deadline: float | None = None) -> None:
            self.stops.append(deadline)

    constructed_schedules: list[RecordingScheduledLoop] = []

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "WorkerStepReadinessLoop", RecordingReadinessLoop)
    monkeypatch.setattr(loops, "ScheduledTicketLoop", RecordingScheduledLoop)
    handle = _start(config, fake_clock)
    try:
        loop = cast(RecordingReadinessLoop, handle.worker_step_readiness_loop)
        schedule_loop = cast(RecordingScheduledLoop, handle.scheduled_ticket_loop)
        assert loop is constructed[0]
        assert schedule_loop is constructed_schedules[0]
        assert loop.started == [config.tick_seconds]
        assert schedule_loop.started == [config.tick_seconds]
        assert loop.kwargs["boundary_hour"] == config.boundary_hour
        assert schedule_loop.kwargs["boundary_hour"] == config.boundary_hour
        change_signal.emit()
        assert loop.wakes == 1
    finally:
        asyncio.run(handle.stop(deadline=123.0))
    # Stopping takes the loop off the signal, so a later commit cannot wake a stopped loop.
    change_signal.emit()
    assert loop.wakes == 1
    # The loop drains under the same deadline, and only then is the lock given back.
    assert loop.stops == [123.0]
    assert schedule_loop.stops == [123.0]
    assert released == [config.dispatcher_lock_path]


def test_a_loop_that_fails_to_start_releases_the_lock_and_leaves_nothing_listening(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    stopped: list[str] = []
    released: list[str] = []

    class BrokenLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def wake(self) -> None:
            return None

        def start(self, _interval: int) -> None:
            raise RuntimeError("start failed")

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            stopped.append("loop")

    class RecordingScheduledLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def start(self, _interval: int) -> None:
            stopped.append("schedule-started")

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            stopped.append("schedule-stopped")

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "WorkerStepReadinessLoop", BrokenLoop)
    monkeypatch.setattr(loops, "ScheduledTicketLoop", RecordingScheduledLoop)
    subscribers_before = change_signal.subscriber_count()
    handle = _start(config, fake_clock)
    try:
        assert handle.worker_step_readiness_loop is None
        assert handle.scheduled_ticket_loop is None
        assert stopped == ["schedule-started", "loop", "schedule-stopped"]
        assert released == [config.dispatcher_lock_path]
        assert change_signal.subscriber_count() == subscribers_before
    finally:
        asyncio.run(handle.stop())


def test_stopping_twice_stops_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float | None]] = []

    class Target:
        def stop(self, *, deadline: float | None = None) -> None:
            calls.append(("loop", deadline))

    monkeypatch.setattr(loops, "release_machine_lock", lambda _path: calls.append(("lock", None)))
    handle = loops.BackgroundLoops(cast(Any, Target()), "polling.lock")
    asyncio.run(handle.stop(deadline=123.0))
    asyncio.run(handle.stop(deadline=456.0))
    assert calls == [("loop", 123.0), ("lock", None)]
