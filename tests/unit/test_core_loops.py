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
from planner.core.db import connect
from planner.core.server import create_app
from planner.minds import config as minds_config
from planner.minds.fake import FakeGateway
from planner.minds.shared_gateway import SharedGateway


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

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert handle.employee_step_runner is not None
        assert handle.ticket_readiness_loop is None
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
            constructed.append(self)

        def poke(self, _key=None) -> None:
            return None

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
    monkeypatch.setattr(minds_config, "resolve_planner_home", lambda: tmp_path / "home")
    monkeypatch.setattr(minds_config, "provision_planner_home_skills", lambda _home: None)

    def conn_factory():
        return connect(config.db_path)

    app = create_app(config, fake_clock, build_adapters(config), conn_factory)
    from fastapi.testclient import TestClient

    with TestClient(app):
        assert app.state.employee_step_runner is runtime.employee_step_runner

    assert order == ["runtime.stop", "worker.shutdown", "chief.shutdown"]
