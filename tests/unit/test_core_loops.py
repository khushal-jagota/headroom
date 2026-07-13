from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from planner.core import loops
from planner.core import server as server_module
from planner.core.adapters.registry import build_adapters
from planner.core.clock import TestClock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.minds import config as minds_config
from planner.minds.fake import FakeGateway
from planner.minds.shared_gateway import SharedGateway
from planner.runtime.readiness_doorbell import LoopReadinessDoorbell, NoOpReadinessDoorbell


def test_dispatch_disabled_keeps_employee_runner_without_acquiring_polling_lock(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "dispatcher.lock"
    config = load_config(
        path=None,
        env={
            "PLAN_DISPATCH_ENABLED": "0",
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(lock_path),
        },
    )

    def fail_if_called(path: str) -> bool:
        raise AssertionError(f"lock should not be acquired when dispatch is disabled: {path}")

    monkeypatch.setattr(loops, "ensure_machine_lock", fail_if_called)
    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert handle.employee_step_runner is not None
        assert handle.ticket_readiness_loop is None
        assert isinstance(handle.readiness_doorbell, NoOpReadinessDoorbell)
        assert handle.employee_step_runner._readiness_doorbell is handle.readiness_doorbell
        assert not lock_path.exists()
    finally:
        asyncio.run(handle.stop())


def test_polling_lock_owned_elsewhere_keeps_employee_runner(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: False)

    def fail_release(path: str) -> None:
        raise AssertionError(f"lock loser must not release another process's lock: {path}")

    monkeypatch.setattr(loops, "release_machine_lock", fail_release)

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert handle.employee_step_runner is not None
        assert handle.ticket_readiness_loop is None
        assert isinstance(handle.readiness_doorbell, NoOpReadinessDoorbell)
        assert handle.employee_step_runner._readiness_doorbell is handle.readiness_doorbell
    finally:
        asyncio.run(handle.stop())


def test_lock_winner_composes_readiness_loop_with_employee_runner(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    constructed: list[Any] = []
    released: list[str] = []

    class RecordingReadinessLoop:
        def __init__(self, _db_path, _clock, runner, **_kwargs) -> None:
            self.runner = runner
            self.started: list[int] = []
            self.wakes = 0
            constructed.append(self)

        def wake(self) -> None:
            self.wakes += 1

        def start(self, interval: int) -> None:
            self.started.append(interval)

        def stop(self) -> None:
            return None

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "TicketReadinessLoop", RecordingReadinessLoop)

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert handle.ticket_readiness_loop is constructed[0]
        assert constructed[0].runner is handle.employee_step_runner
        assert constructed[0].started == [config.tick_seconds]
        assert handle.employee_step_runner._readiness_doorbell is handle.readiness_doorbell
        handle.readiness_doorbell.ring()
        assert constructed[0].wakes == 1
    finally:
        asyncio.run(handle.stop())
    assert released == [config.dispatcher_lock_path]


def test_readiness_loop_start_failure_releases_lock_but_keeps_runner(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    released: list[str] = []

    class BrokenReadinessLoop:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("cannot start poller")

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "TicketReadinessLoop", BrokenReadinessLoop)

    fake = FakeGateway({})
    gateway = SharedGateway(
        hermes_python=sys.executable,
        home=tmp_path / "hermes-home",
        worker_role="planning-worker",
        spawn=fake.spawn,
        base_env={},
    )

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=gateway,
    )
    try:
        assert handle.employee_step_runner is not None
        assert handle.ticket_readiness_loop is None
        assert isinstance(handle.readiness_doorbell, NoOpReadinessDoorbell)
        assert released == [config.dispatcher_lock_path]
        handoff = handle.employee_step_runner.reserve_revision(
            "direct-revision-ticket", "Revise this result."
        )
        handoff.cancel()
        assert handle.employee_step_runner.wait_idle(10.0)
        assert fake.sent_methods() == []
    finally:
        asyncio.run(handle.stop())
        gateway.shutdown()
    assert released == [config.dispatcher_lock_path]


def test_partial_loop_start_failure_stops_loop_and_rebuilds_runner_with_noop(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    runners: list[Any] = []
    stopped: list[str] = []
    released: list[str] = []

    class RecordingRunner:
        def __init__(self, *_args, readiness_doorbell, **_kwargs) -> None:
            self.readiness_doorbell = readiness_doorbell
            runners.append(self)

        def stop(self) -> None:
            stopped.append(f"runner-{runners.index(self)}")

    class PartiallyStartedLoop:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def wake(self) -> None:
            return None

        def start(self, _interval: int) -> None:
            raise RuntimeError("start failed after construction")

        def stop(self) -> None:
            stopped.append("loop")

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "EmployeeStepRunner", RecordingRunner)
    monkeypatch.setattr(loops, "TicketReadinessLoop", PartiallyStartedLoop)

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert len(runners) == 2
        assert isinstance(runners[0].readiness_doorbell, LoopReadinessDoorbell)
        assert handle.employee_step_runner is runners[1]
        assert handle.ticket_readiness_loop is None
        assert isinstance(handle.readiness_doorbell, NoOpReadinessDoorbell)
        assert runners[1].readiness_doorbell is handle.readiness_doorbell
        assert stopped == ["loop", "runner-0"]
        assert released == [config.dispatcher_lock_path]
    finally:
        asyncio.run(handle.stop())


def test_background_stop_orders_loop_runner_then_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class RecordingLoop:
        def stop(self) -> None:
            order.append("loop")

    class RecordingRunner:
        def stop(self) -> None:
            order.append("runner")

    monkeypatch.setattr(loops, "release_machine_lock", lambda _path: order.append("lock"))
    handle = loops.BackgroundLoops(
        [],
        cast(Any, RecordingRunner()),
        cast(Any, RecordingLoop()),
        "polling.lock",
    )

    asyncio.run(handle.stop())
    asyncio.run(handle.stop())

    assert order == ["loop", "runner", "lock"]


def test_server_lifespan_drains_runtime_before_shutting_down_gateways(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(tmp_path / "planning.db"),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_GATEWAY_ADAPTER": "fake",
        },
    )

    class RecordingGateway:
        def __init__(self, name: str) -> None:
            self.name = name

        def shutdown(self) -> None:
            order.append(f"{self.name}.shutdown")

    class RecordingRuntime:
        employee_step_runner = object()
        ticket_readiness_loop = None
        readiness_doorbell = NoOpReadinessDoorbell()

        async def stop(self) -> None:
            order.append("runtime.stop")

    runtime = RecordingRuntime()

    def start_runtime(*_args, **_kwargs):
        return runtime

    real_import_module = server_module.importlib.import_module
    monkeypatch.setattr(
        server_module.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(start_background_loops=start_runtime)
            if name == "planner.core.loops"
            else real_import_module(name)
        ),
    )
    monkeypatch.setattr(
        server_module,
        "_build_role_gateways",
        lambda **_kwargs: (RecordingGateway("worker"), RecordingGateway("chief")),
    )
    monkeypatch.setattr(minds_config, "resolve_hermes_python", lambda: Path(sys.executable))
    monkeypatch.setattr(
        minds_config,
        "resolve_planner_home",
        lambda *, default: tmp_path / "home",
    )
    monkeypatch.setattr(minds_config, "provision_planner_home_skills", lambda _home: None)

    def conn_factory():
        return connect(config.db_path)

    # Mirror the real boot path (cli/main.py creates the schema before the app
    # starts) so the lifespan's startup integrity audit finds a tickets table.
    schema_conn = conn_factory()
    create_schema(schema_conn)
    schema_conn.close()

    app = create_app(config, fake_clock, build_adapters(config), conn_factory)
    from fastapi.testclient import TestClient

    with TestClient(app):
        assert app.state.employee_step_runner is runtime.employee_step_runner
        assert app.state.readiness_doorbell is runtime.readiness_doorbell

    assert order == ["runtime.stop", "worker.shutdown", "chief.shutdown"]


def test_server_lifespan_uses_absolute_database_adjacent_hermes_home_across_cwds(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_root = tmp_path / "configured-data"
    other_worktree = tmp_path / "other-worktree"
    other_worktree.mkdir()
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(database_root / "planning.db"),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_GATEWAY_ADAPTER": "fake",
        },
    )
    provisioned_homes: list[Path] = []
    gateway_homes: list[Path] = []

    class RecordingGateway:
        def shutdown(self) -> None:
            return None

    class RecordingRuntime:
        employee_step_runner = object()
        ticket_readiness_loop = None
        readiness_doorbell = NoOpReadinessDoorbell()

        async def stop(self) -> None:
            return None

    real_import_module = server_module.importlib.import_module
    monkeypatch.setattr(
        server_module.importlib,
        "import_module",
        lambda name: (
            SimpleNamespace(start_background_loops=lambda *_args, **_kwargs: RecordingRuntime())
            if name == "planner.core.loops"
            else real_import_module(name)
        ),
    )

    def build_role_gateways(**kwargs):
        gateway_homes.extend([kwargs["planner_home"], kwargs["planner_home"]])
        return RecordingGateway(), RecordingGateway()

    monkeypatch.setattr(server_module, "_build_role_gateways", build_role_gateways)
    monkeypatch.setattr(minds_config, "resolve_hermes_python", lambda: Path(sys.executable))
    monkeypatch.setattr(
        minds_config,
        "provision_planner_home_skills",
        provisioned_homes.append,
    )
    monkeypatch.delenv("PLAN_HERMES_HOME", raising=False)
    monkeypatch.chdir(other_worktree)

    def conn_factory():
        return connect(config.db_path)

    # Mirror the real boot path (schema created before the app starts) so the
    # lifespan's startup integrity audit finds a tickets table.
    Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
    schema_conn = conn_factory()
    create_schema(schema_conn)
    schema_conn.close()

    app = create_app(config, fake_clock, build_adapters(config), conn_factory)
    from fastapi.testclient import TestClient

    with TestClient(app):
        pass

    expected_home = (database_root / "hermes-home").resolve(strict=False)
    assert provisioned_homes == [expected_home]
    assert gateway_homes == [expected_home, expected_home]
    assert expected_home != (other_worktree / "data/hermes-home").resolve(strict=False)
