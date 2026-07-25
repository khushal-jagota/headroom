from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from planner.core import change_signal, loops
from planner.core.clock import TestClock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.runtime.employee_step_repository import SqliteEmployeeStepRepository
from planner.runtime.step_gateway import StepGateway
from planner.tickets import data as tickets_data


def _config(tmp_path: Path, *, dispatch: bool = True):
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


def _gateway() -> StepGateway:
    return cast(StepGateway, object())


def test_dispatch_disabled_keeps_runner_without_acquiring_lock(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, dispatch=False)

    def fail_if_called(path: str) -> bool:
        raise AssertionError(f"disabled discovery must not acquire {path}")

    monkeypatch.setattr(loops, "ensure_machine_lock", fail_if_called)
    subscribers_before = change_signal.subscriber_count()
    handle = loops.start_background_loops(config, fake_clock, step_gateway=_gateway())
    try:
        assert handle.automatic_employee_step_discovery_loop is None
        assert change_signal.subscriber_count() == subscribers_before
    finally:
        asyncio.run(handle.stop())


def test_polling_lock_loser_keeps_runner_and_does_not_release_foreign_lock(
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
    handle = loops.start_background_loops(config, fake_clock, step_gateway=_gateway())
    try:
        assert handle.automatic_employee_step_discovery_loop is None
        assert change_signal.subscriber_count() == subscribers_before
    finally:
        asyncio.run(handle.stop())


def test_lock_winner_composes_discovery_and_wakes_it_from_the_change_signal(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    constructed: list[Any] = []
    released: list[str] = []

    class RecordingDiscoveryLoop:
        def __init__(self, _db_path, _clock, runner, **_kwargs) -> None:
            self.runner = runner
            self.started: list[int] = []
            self.wakes = 0
            constructed.append(self)

        def wake(self) -> None:
            self.wakes += 1

        def start(self, interval: int) -> None:
            self.started.append(interval)

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "AutomaticEmployeeStepDiscoveryLoop", RecordingDiscoveryLoop)
    handle = loops.start_background_loops(config, fake_clock, step_gateway=_gateway())
    try:
        assert handle.automatic_employee_step_discovery_loop is constructed[0]
        assert constructed[0].runner is handle.employee_step_runner
        assert constructed[0].started == [config.tick_seconds]
        change_signal.emit()
        assert constructed[0].wakes == 1
    finally:
        asyncio.run(handle.stop())
    # Stopping takes the loop off the signal, so a later commit cannot wake a stopped loop.
    change_signal.emit()
    assert constructed[0].wakes == 1
    assert released == [config.dispatcher_lock_path]


def test_partial_discovery_start_failure_stops_candidates_and_rebuilds_runner(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    runners: list[Any] = []
    stopped: list[str] = []
    released: list[str] = []

    class RecordingRunner:
        def __init__(self, *_args, **_kwargs) -> None:
            runners.append(self)

        def recover_running_step(self, _ticket_id: str) -> None:
            return None

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            stopped.append(f"runner-{runners.index(self)}")

    class BrokenLoop:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def wake(self) -> None:
            return None

        def start(self, _interval: int) -> None:
            raise RuntimeError("start failed")

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            stopped.append("loop")

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "EmployeeStepRunner", RecordingRunner)
    monkeypatch.setattr(loops, "AutomaticEmployeeStepDiscoveryLoop", BrokenLoop)
    subscribers_before = change_signal.subscriber_count()
    handle = loops.start_background_loops(config, fake_clock, step_gateway=_gateway())
    try:
        assert len(runners) == 2
        assert handle.employee_step_runner is runners[1]
        assert stopped == ["loop", "runner-0"]
        assert released == [config.dispatcher_lock_path]
        # A discovery loop that never started is not left listening for commits.
        assert change_signal.subscriber_count() == subscribers_before
    finally:
        asyncio.run(handle.stop())


def test_background_stop_orders_discovery_runner_and_lock_under_one_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float | None]] = []

    class Target:
        def __init__(self, name: str) -> None:
            self.name = name

        def stop(self, *, deadline: float | None = None) -> None:
            calls.append((self.name, deadline))

    monkeypatch.setattr(
        loops, "release_machine_lock", lambda _path: calls.append(("lock", None))
    )
    handle = loops.BackgroundLoops(
        [],
        cast(Any, Target("runner")),
        cast(Any, Target("discovery")),
        "polling.lock",
    )
    asyncio.run(handle.stop(deadline=123.0))
    asyncio.run(handle.stop(deadline=456.0))
    assert calls == [("discovery", 123.0), ("runner", 123.0), ("lock", None)]


def test_startup_stale_handoff_settles_only_nonrunning_ticket_employee_steps(
    tmp_path: Path,
    fake_clock: TestClock,
) -> None:
    config = _config(tmp_path, dispatch=False)
    conn = connect(config.db_path)
    try:
        stale = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Stale",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        active = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Active",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        conn.execute(
            "UPDATE tickets SET ticket_status = 'agent' WHERE id = ?",
            (active.id,),
        )
        repository = SqliteEmployeeStepRepository()
        stale_run = repository.start(conn, stale.id, now=1)
        active_run = repository.start(conn, active.id, now=1)
    finally:
        conn.close()

    loops._settle_stale_employee_steps_after_ticket_handoff(config, fake_clock)
    conn = connect(config.db_path)
    try:
        repository = SqliteEmployeeStepRepository()
        assert repository.require(conn, stale_run.employee_step_id).status == "interrupted"
        assert repository.require(conn, active_run.employee_step_id).status == "running"
    finally:
        conn.close()


def test_startup_recovers_each_running_ticket_after_stale_cleanup(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path, dispatch=False)
    conn = connect(config.db_path)
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title="Recover",
            actor="human",
            now=1,
            title_max_chars=200,
        )
        conn.execute(
            "UPDATE tickets SET ticket_status = 'agent' WHERE id = ?",
            (ticket.id,),
        )
        SqliteEmployeeStepRepository().start(
            conn, ticket.id, now=1, employee_session_id="session-1"
        )
    finally:
        conn.close()
    recovered: list[str] = []

    class RecordingRunner:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def recover_running_step(self, ticket_id: str) -> None:
            recovered.append(ticket_id)

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline

    monkeypatch.setattr(loops, "EmployeeStepRunner", RecordingRunner)
    handle = loops.start_background_loops(config, fake_clock, step_gateway=_gateway())
    try:
        assert recovered == [ticket.id]
    finally:
        asyncio.run(handle.stop())
