from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from planner.chat import data as chat_data
from planner.chat.contracts import HumanChatCompletion
from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.core import loops
from planner.core import server as server_module
from planner.core.adapters.registry import build_adapters
from planner.core.clock import TestClock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.days.logic import dates
from planner.minds import config as minds_config
from planner.minds.fake import FakeGateway
from planner.minds.shared_gateway import EntityRoutingGateway, SharedGateway
from planner.runtime import automatic_employee_step_eligibility
from planner.runtime.automatic_employee_step_eligibility_wake import (
    LoopAutomaticEmployeeStepEligibilityWake,
    NoOpAutomaticEmployeeStepEligibilityWake,
)
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, AtCap, EmployeeSessionIdTransition


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
        assert handle.automatic_employee_step_discovery_loop is None
        assert isinstance(
            handle.automatic_employee_step_eligibility_wake,
            NoOpAutomaticEmployeeStepEligibilityWake,
        )
        assert (
            handle.employee_step_runner._automatic_employee_step_eligibility_wake
            is handle.automatic_employee_step_eligibility_wake
        )
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
        assert handle.automatic_employee_step_discovery_loop is None
        assert isinstance(
            handle.automatic_employee_step_eligibility_wake,
            NoOpAutomaticEmployeeStepEligibilityWake,
        )
        assert (
            handle.employee_step_runner._automatic_employee_step_eligibility_wake
            is handle.automatic_employee_step_eligibility_wake
        )
    finally:
        asyncio.run(handle.stop())


def test_lock_winner_composes_automatic_discovery_with_employee_runner(
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
            return None

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "AutomaticEmployeeStepDiscoveryLoop", RecordingDiscoveryLoop)

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert handle.automatic_employee_step_discovery_loop is constructed[0]
        assert constructed[0].runner is handle.employee_step_runner
        assert constructed[0].started == [config.tick_seconds]
        assert (
            handle.employee_step_runner._automatic_employee_step_eligibility_wake
            is handle.automatic_employee_step_eligibility_wake
        )
        handle.automatic_employee_step_eligibility_wake.wake()
        assert constructed[0].wakes == 1
    finally:
        asyncio.run(handle.stop())
    assert released == [config.dispatcher_lock_path]


def test_discovery_start_failure_releases_lock_but_keeps_runner(
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

    class BrokenDiscoveryLoop:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("cannot start poller")

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "AutomaticEmployeeStepDiscoveryLoop", BrokenDiscoveryLoop)

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
        assert handle.automatic_employee_step_discovery_loop is None
        assert isinstance(
            handle.automatic_employee_step_eligibility_wake,
            NoOpAutomaticEmployeeStepEligibilityWake,
        )
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
        def __init__(self, *_args, automatic_employee_step_eligibility_wake, **_kwargs) -> None:
            self.automatic_employee_step_eligibility_wake = automatic_employee_step_eligibility_wake
            runners.append(self)

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            stopped.append(f"runner-{runners.index(self)}")

    class PartiallyStartedLoop:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def wake(self) -> None:
            return None

        def start(self, _interval: int) -> None:
            raise RuntimeError("start failed after construction")

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            stopped.append("loop")

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: True)
    monkeypatch.setattr(loops, "release_machine_lock", released.append)
    monkeypatch.setattr(loops, "EmployeeStepRunner", RecordingRunner)
    monkeypatch.setattr(loops, "AutomaticEmployeeStepDiscoveryLoop", PartiallyStartedLoop)

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        assert len(runners) == 2
        assert isinstance(
            runners[0].automatic_employee_step_eligibility_wake,
            LoopAutomaticEmployeeStepEligibilityWake,
        )
        assert handle.employee_step_runner is runners[1]
        assert handle.automatic_employee_step_discovery_loop is None
        assert isinstance(
            handle.automatic_employee_step_eligibility_wake,
            NoOpAutomaticEmployeeStepEligibilityWake,
        )
        assert (
            runners[1].automatic_employee_step_eligibility_wake
            is handle.automatic_employee_step_eligibility_wake
        )
        assert stopped == ["loop", "runner-0"]
        assert released == [config.dispatcher_lock_path]
    finally:
        asyncio.run(handle.stop())


def test_background_stop_orders_loop_runner_then_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    class RecordingLoop:
        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            order.append("loop")

    class RecordingRunner:
        def stop(self, *, deadline: float | None = None) -> None:
            del deadline
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

        def shutdown(self, *, deadline: float | None = None) -> None:
            del deadline
            order.append(f"{self.name}.shutdown")

    class RecordingRuntime:
        employee_step_runner = object()
        automatic_employee_step_discovery_loop = None
        automatic_employee_step_eligibility_wake = NoOpAutomaticEmployeeStepEligibilityWake()

        async def stop(self, *, deadline: float | None = None) -> None:
            del deadline
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
        assert (
            app.state.automatic_employee_step_eligibility_wake
            is runtime.automatic_employee_step_eligibility_wake
        )

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
        def shutdown(self, *, deadline: float | None = None) -> None:
            del deadline
            return None

    class RecordingRuntime:
        employee_step_runner = object()
        automatic_employee_step_discovery_loop = None
        automatic_employee_step_eligibility_wake = NoOpAutomaticEmployeeStepEligibilityWake()

        async def stop(self, *, deadline: float | None = None) -> None:
            del deadline
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


def _create_running_ticket(
    db_path: Path,
    fake_clock: TestClock,
    *,
    employee_session_id: str = "stored-key",
) -> str:
    conn = connect(str(db_path))
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Recover me",
        actor="human",
        now=0,
        title_max_chars=200,
    )
    tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=0,
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )
    planning_day_id = dates.resolve_day_id("today", fake_clock.now(), 5)
    days_data.add_day_ticket(conn, planning_day_id, ticket.id, 0)
    claimed = tickets_data.claim_automatic_employee_step(
        conn,
        ticket.id,
        planning_day_id_resolver=lambda: planning_day_id,
        eligibility_check=(
            automatic_employee_step_eligibility.is_eligible_for_automatic_employee_step
        ),
        now=1,
    )
    assert claimed is not None
    tickets_data.claim_running_step_employee_session_id(
        conn,
        ticket.id,
        transition=EmployeeSessionIdTransition(None, employee_session_id),
        now=2,
    )
    conn.close()
    return ticket.id


@pytest.mark.parametrize("owns_polling_lock", [True, False])
def test_startup_recovers_running_tickets_before_optional_automatic_discovery(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
    owns_polling_lock: bool,
) -> None:
    db_path = tmp_path / "planning.db"
    ticket_id = _create_running_ticket(db_path, fake_clock)
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(db_path),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    order: list[str] = []

    class RecordingRunner:
        def __init__(
            self, *_args, automatic_employee_step_eligibility_wake, **_kwargs
        ) -> None:
            self.automatic_employee_step_eligibility_wake = (
                automatic_employee_step_eligibility_wake
            )

        def recover_running_step(self, recovered_ticket_id: str) -> None:
            order.append(f"recover:{recovered_ticket_id}")

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline

    class RecordingDiscoveryLoop:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def wake(self) -> None:
            return None

        def start(self, _interval: int) -> None:
            order.append("discovery.start")

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: owns_polling_lock)
    monkeypatch.setattr(loops, "release_machine_lock", lambda _path: None)
    monkeypatch.setattr(loops, "EmployeeStepRunner", RecordingRunner)
    monkeypatch.setattr(
        loops,
        "AutomaticEmployeeStepDiscoveryLoop",
        RecordingDiscoveryLoop,
    )

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        expected = [f"recover:{ticket_id}"]
        if owns_polling_lock:
            expected.append("discovery.start")
        assert order == expected
        assert (handle.automatic_employee_step_discovery_loop is not None) is owns_polling_lock
    finally:
        asyncio.run(handle.stop())


def test_startup_settles_worker_turn_after_proposal_handoff_without_reprompt(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "planning.db"
    ticket_id = _create_running_ticket(db_path, fake_clock)
    conn = connect(str(db_path))
    turn = chat_data.start_turn(
        conn,
        ticket_id,
        origin="worker",
        mode="worker_step",
        visible_role="worker",
        visible_text="original prompt",
        output_role="assistant",
        phase="responding",
        activity_label=None,
        now=2,
    )
    chat_data.attach_session_key(
        conn,
        turn.id,
        entity_id=ticket_id,
        session_key="stored-key",
        now=2,
    )
    chat_data.append_turn_output(
        conn,
        turn.id,
        entity_id=ticket_id,
        delta="partial visible output",
        now=3,
    )
    tickets_data.file_proposal(
        conn,
        ticket_id,
        field="success",
        body="done",
        actor="agent",
        now=4,
    )
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(db_path),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
        },
    )
    recovered: list[str] = []

    class RecordingRunner:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def recover_running_step(self, recovered_ticket_id: str) -> None:
            recovered.append(recovered_ticket_id)

        def stop(self, *, deadline: float | None = None) -> None:
            del deadline

    monkeypatch.setattr(loops, "ensure_machine_lock", lambda _path: False)
    monkeypatch.setattr(loops, "EmployeeStepRunner", RecordingRunner)

    handle = loops.start_background_loops(
        config,
        fake_clock,
        shared_gateway=cast(SharedGateway, object()),
    )
    try:
        inspect = connect(str(db_path))
        try:
            turn_row = inspect.execute(
                "SELECT status, output_text FROM chat_turns WHERE id = ?", (turn.id,)
            ).fetchone()
            messages = inspect.execute(
                "SELECT role, text FROM chat_messages WHERE entity_id = ? ORDER BY id",
                (ticket_id,),
            ).fetchall()
        finally:
            inspect.close()
        assert recovered == []
        assert (turn_row["status"], turn_row["output_text"]) == (
            "interrupted",
            "partial visible output",
        )
        assert [(message["role"], message["text"]) for message in messages] == [
            ("worker", "original prompt"),
            ("assistant", "partial visible output"),
        ]
    finally:
        asyncio.run(handle.stop())


def test_background_stop_passes_one_absolute_deadline_to_discovery_and_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadlines: list[tuple[str, float | None]] = []

    class RecordingDiscovery:
        def stop(self, *, deadline: float | None = None) -> None:
            deadlines.append(("discovery", deadline))

    class RecordingRunner:
        def stop(self, *, deadline: float | None = None) -> None:
            deadlines.append(("runner", deadline))

    monkeypatch.setattr(loops, "_monotonic", lambda: 100.0)
    handle = loops.BackgroundLoops(
        [],
        cast(Any, RecordingRunner()),
        cast(Any, RecordingDiscovery()),
        shutdown_grace_seconds=5.0,
    )

    asyncio.run(handle.stop())

    assert deadlines == [("discovery", 105.0), ("runner", 105.0)]


def test_background_stop_propagates_internal_type_error_without_retry() -> None:
    calls: list[float | None] = []

    class BrokenRunner:
        def stop(self, *, deadline: float | None = None) -> None:
            calls.append(deadline)
            raise TypeError("internal shutdown bug")

    handle = loops.BackgroundLoops([], cast(Any, BrokenRunner()))
    with pytest.raises(TypeError, match="internal shutdown bug"):
        asyncio.run(handle.stop(deadline=123.0))
    assert calls == [123.0]


def test_entity_routing_gateway_attempts_every_unique_gateway_before_reraising_the_first_failure(
) -> None:
    class RecordingGateway:
        def __init__(self, failure: Exception) -> None:
            self.deadlines: list[float | None] = []
            self.failure = failure

        def shutdown(self, *, deadline: float | None = None) -> None:
            self.deadlines.append(deadline)
            raise self.failure

    first_failure = RuntimeError("worker cleanup failed")
    second_failure = ValueError("chief cleanup failed")
    default = RecordingGateway(first_failure)
    chief = RecordingGateway(second_failure)
    router = EntityRoutingGateway(
        cast(SharedGateway, default),
        {
            CHIEF_OF_STAFF_ENTITY_ID: cast(SharedGateway, chief),
            "alias": cast(SharedGateway, chief),
        },
    )

    with pytest.raises(RuntimeError) as caught:
        router.shutdown(deadline=456.0)

    assert caught.value is first_failure
    assert default.deadlines == [456.0]
    assert chief.deadlines == [456.0]


def test_server_startup_human_recovery_does_not_block_background_loops(
    tmp_path: Path,
    fake_clock: TestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "planning.db"
    config = load_config(
        path=None,
        env={
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_GATEWAY_ADAPTER": "fake",
        },
    )
    conn = connect(config.db_path)
    create_schema(conn)
    chat_data.start_turn(
        conn,
        CHIEF_OF_STAFF_ENTITY_ID,
        origin="human",
        mode="message",
        visible_role="human",
        visible_text="hello",
        output_role="assistant",
        phase="responding",
        activity_label=None,
        now=1,
    )
    conn.execute(
        "INSERT INTO agent_chat_sessions (id, chat_session_key, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (CHIEF_OF_STAFF_ENTITY_ID, "chief-session", 1, 1),
    )
    active = chat_data.read_active_turn(conn, CHIEF_OF_STAFF_ENTITY_ID)
    assert active is not None
    chat_data.attach_session_key(
        conn,
        active.id,
        entity_id=CHIEF_OF_STAFF_ENTITY_ID,
        session_key="chief-session",
        now=1,
    )
    conn.close()
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    class RecordingGateway:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self) -> None:
            order.append(f"{self.name}.start")

        def run_human_turn(
            self,
            session_key,
            entity_id,
            text,
            mode,
            bind_session_key,
            image_paths=(),
            *,
            require_existing_session=False,
        ):  # noqa: ANN001, ANN201
            del entity_id, text, mode, image_paths
            order.append(f"{self.name}.run_human_turn")
            assert session_key == "chief-session"
            assert require_existing_session is True
            entered.set()
            assert release.wait(5.0)
            bind_session_key("chief-session")
            yield HumanChatCompletion("continued", "assistant")

        def shutdown(self, *, deadline: float | None = None) -> None:
            del deadline
            order.append(f"{self.name}.shutdown")

    class RecordingRuntime:
        employee_step_runner = object()
        automatic_employee_step_discovery_loop = None
        automatic_employee_step_eligibility_wake = NoOpAutomaticEmployeeStepEligibilityWake()

        async def stop(self, *, deadline: float | None = None) -> None:
            del deadline
            order.append("runtime.stop")

    def start_runtime(*_args, **_kwargs):
        order.append("runtime.start")
        return RecordingRuntime()

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
    monkeypatch.setattr(minds_config, "resolve_planner_home", lambda *, default: tmp_path / "home")
    monkeypatch.setattr(minds_config, "provision_planner_home_skills", lambda _home: None)

    app = create_app(
        config,
        fake_clock,
        build_adapters(config),
        lambda: connect(config.db_path),
    )
    errors: list[BaseException] = []

    def run_client() -> None:
        from fastapi.testclient import TestClient

        try:
            with TestClient(app):
                order.append("client.entered")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    thread = threading.Thread(target=run_client)
    thread.start()
    assert entered.wait(5.0)
    deadline = time.monotonic() + 1.0
    while "runtime.start" not in order and time.monotonic() < deadline:
        time.sleep(0.01)
    started_before_release = "runtime.start" in order
    release.set()
    thread.join(5.0)

    assert not thread.is_alive()
    assert errors == []
    assert started_before_release is True
    assert "client.entered" in order


def test_test_mode_startup_recovery_switch_uses_configured_fake_adapter(
    tmp_path: Path,
    fake_clock: TestClock,
) -> None:
    db_path = tmp_path / "planning.db"
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_GATEWAY_ADAPTER": "fake",
        },
    )
    conn = connect(config.db_path)
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Recover test mode Chat",
        actor="test",
        now=1,
        title_max_chars=200,
    )
    tickets_data.finish_run_if_still_running_step(
        conn,
        ticket.id,
        employee_session_transition=EmployeeSessionIdTransition(None, "stored-session"),
        now=2,
    )
    turn = chat_data.start_turn(
        conn,
        ticket.id,
        origin="human",
        mode="message",
        visible_role="human",
        visible_text="original prompt",
        output_role="assistant",
        phase="responding",
        activity_label=None,
        now=3,
    )
    chat_data.attach_session_key(
        conn,
        turn.id,
        entity_id=ticket.id,
        session_key="stored-session",
        now=3,
    )
    chat_data.append_turn_output(
        conn,
        turn.id,
        entity_id=ticket.id,
        delta="partial before restart",
        now=4,
    )
    conn.close()

    app = create_app(
        config,
        fake_clock,
        build_adapters(config),
        lambda: connect(config.db_path),
    )
    from fastapi.testclient import TestClient

    with TestClient(app):
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            inspect = connect(config.db_path)
            try:
                if chat_data.read_active_turn(inspect, ticket.id) is None:
                    break
            finally:
                inspect.close()
            time.sleep(0.01)

    inspect = connect(config.db_path)
    try:
        state = chat_data.read_state(inspect, ticket.id)
    finally:
        inspect.close()
    assert state.active_turn is None
    assert [message.text for message in state.messages] == [
        "original prompt",
        "partial before restart",
        "Panels restarted. Continue the interrupted response in this existing session.",
        "echo: Panels restarted. Continue the interrupted response in this existing session.",
    ]
