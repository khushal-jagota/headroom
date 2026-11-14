"""Legacy background admission cleans partial starts; publication preparation raises."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

import pytest

from planner.conversation.contracts import ConversationSystem
from planner.conversation.workspace_admission import WorkspaceAdmission
from planner.core import loops
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.goals.scheduling import GoalCompletionParticipant
from planner.measure_sources.contracts import SourceRuntime
from planner.measure_sources.participants import SlotParticipant
from planner.measure_sources.registry import build_participants
from planner.measure_sources.schedule_loop import SourceScheduleLoop
from planner.measure_sources.scheduling import SourceScheduleParticipant
from planner.runtime.lock import ensure_machine_lock


def _assert_lock(path: str, *, held: bool) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import fcntl,sys; f=open(sys.argv[1], 'a'); "
            "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)",
            path,
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == (1 if held else 0), result.stderr.decode()


def test_explicit_registered_participants_share_admission_and_one_lifespan(tmp_path: Path) -> None:
    """The test seam runs all production participants under the actual barrier."""
    cfg = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(tmp_path / "participants.db"),
            "PLAN_CREDENTIAL_KEY_PATH": str(tmp_path / "secrets" / "key"),
        },
    )
    with closing(connect(cfg.db_path)) as conn:
        create_schema(conn)
    clock = build_clock(cfg)
    admission = WorkspaceAdmission()
    factory_admissions: list[WorkspaceAdmission] = []
    registered: tuple[SlotParticipant, ...] = ()
    starts = [0, 0]
    stops = [0, 0]
    polls = [0, 0]
    deadlines: list[float | None] = []
    source_polled = threading.Event()

    class TrackedParticipant:
        def __init__(self, position: int, participant: SlotParticipant) -> None:
            self.position = position
            self.participant = participant

        def start(self) -> None:
            starts[self.position] += 1
            self.participant.start()

        def poll(self, conn: sqlite3.Connection, *, local_now: datetime, now: int) -> None:
            assert admission.active
            polls[self.position] += 1
            self.participant.poll(conn, local_now=local_now, now=now)
            if self.position == 0:
                source_polled.set()

        def stop(self, *, deadline: float | None = None) -> None:
            stops[self.position] += 1
            deadlines.append(deadline)
            self.participant.stop(deadline=deadline)

    def participants_factory(actual: WorkspaceAdmission) -> tuple[SlotParticipant, ...]:
        nonlocal registered
        factory_admissions.append(actual)
        registered = build_participants(
            cfg,
            clock,
            workspace_admission=actual,
            # No source definitions exist, so no runtime executes or requests data.
            source_runtime=cast(SourceRuntime, object()),
        )
        return tuple(
            TrackedParticipant(position, participant)
            for position, participant in enumerate(registered)
        )

    owner = loops.prepare_scheduled_loops(
        cfg, clock, workspace_admission=admission, participants_factory=participants_factory
    )
    assert factory_admissions == [admission]
    assert len(registered) == 2
    assert isinstance(registered[0], SourceScheduleParticipant)
    assert isinstance(registered[1], GoalCompletionParticipant)
    assert owner.worker_step_readiness_loop is None
    assert owner.notification_loop is None and owner.manager_wake_loop is None
    scheduler = owner.source_schedule_loop
    assert scheduler is not None

    async def run() -> None:
        owner.start(60)
        deadline = monotonic() + 5
        try:
            assert await asyncio.to_thread(source_polled.wait, 2)
            assert starts == [1, 1] and stops == [0, 0]
        finally:
            await owner.stop(deadline=deadline)
        await owner.stop(deadline=deadline)
        assert stops == [1, 1]
        assert deadlines == [deadline, deadline]
        assert scheduler._thread is None
        assert not admission.active
        # Closed admission blocks all participant polls, not only source execution.
        assert admission.claim(lambda: None, idle=lambda: True, require_idle=True)
        before = list(polls)
        scheduler.poll_once()
        assert polls == before

    asyncio.run(run())


@pytest.mark.parametrize("participant_fails", [False, True])
def test_stop_drains_siblings_retains_live_schedule_and_retries_lock_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, participant_fails: bool
) -> None:
    """A real retained polling thread forbids handoff, but not independent drains."""
    events: list[str] = []
    entered = threading.Event()
    release = threading.Event()
    first_error = RuntimeError("participant drain failed")
    later_error = RuntimeError("notification drain failed")
    fail_stops = True

    class Participant:
        def start(self) -> None:
            pass

        def stop(self, *, deadline: float | None = None) -> None:
            events.append("participant")
            if fail_stops and participant_fails:
                raise first_error

    class LaterParticipant(Participant):
        def stop(self, *, deadline: float | None = None) -> None:
            events.append("later participant")

    class Sibling:
        def __init__(self, name: str) -> None:
            self.name = name

        def start(self, _interval: int) -> None:
            pass

        def stop(self, *, deadline: float | None = None) -> None:
            events.append(self.name)
            if fail_stops and self.name == "notification":
                raise later_error

    cfg = load_config(path=None, env={"PLAN_TEST_MODE": "1"})
    scheduler = SourceScheduleLoop(
        str(tmp_path / "unused.db"),
        build_clock(cfg),
        participants=cast(tuple[SlotParticipant, ...], (Participant(), LaterParticipant())),
    )

    def blocked_poll() -> list[Any]:
        entered.set()
        assert release.wait(5), "test did not release its polling thread"
        return []

    monkeypatch.setattr(scheduler, "poll_once", blocked_poll)
    lock_path = str(tmp_path / "polling.lock")
    assert ensure_machine_lock(lock_path)
    owner = loops.BackgroundLoops(
        cast(Any, Sibling("worker")),
        lock_path,
        source_schedule_loop=scheduler,
        notification_loop=cast(Any, Sibling("notification")),
        manager_wake_loop=cast(Any, Sibling("manager")),
    )
    monkeypatch.setattr(loops, "_active", owner)
    owner.start(1)

    async def run() -> None:
        nonlocal fail_stops
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            thread = scheduler._thread
            with pytest.raises(RuntimeError) as caught:
                await owner.stop(deadline=monotonic())
            if participant_fails:
                assert caught.value is first_error
                assert any(
                    "schedule clock remains active" in note for note in first_error.__notes__
                )
            else:
                assert "schedule clock remains active" in str(caught.value)
            assert any("notification drain failed" in note for note in caught.value.__notes__)
            assert events == [
                "participant",
                "later participant",
                "notification",
                "manager",
                "worker",
            ]
            assert scheduler._thread is thread
            assert thread is not None and thread.is_alive()
            assert loops._active is owner and not owner._stopped
            _assert_lock(lock_path, held=True)
            with pytest.raises(RuntimeError, match="already running"):
                loops.prepare_background_loops(
                    cfg,
                    build_clock(cfg),
                    conversation_system=cast(ConversationSystem, object()),
                    asyncio_loop=asyncio.get_running_loop(),
                )
            with pytest.raises(RuntimeError, match="cannot start"):
                owner.start(1)
        finally:
            fail_stops = False
            release.set()
            await owner.stop(deadline=monotonic() + 2)
        assert scheduler._thread is None
        assert not thread.is_alive()
        assert loops._active is None and owner._stopped
        _assert_lock(lock_path, held=False)

    asyncio.run(run())


@pytest.mark.parametrize("fail_during_reconciliation", [False, True])
@pytest.mark.parametrize("fail_during_cleanup", [False, True])
def test_legacy_failure_returns_inert_owner_and_releases_every_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_during_reconciliation: bool,
    fail_during_cleanup: bool,
) -> None:
    events: list[str] = []
    fail_stops = fail_during_cleanup

    class Candidate:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self.name = "candidate"

        def reconcile_imported_state(self) -> None:
            events.append("reconcile")
            if fail_during_reconciliation:
                raise RuntimeError("synthetic reconciliation failure")

        def start(self, _seconds: int) -> None:
            events.append("start")
            if events.count("start") == 3:
                raise RuntimeError("synthetic partial admission failure")

        def stop(self, *, deadline: float | None = None) -> None:
            events.append("stop")
            if fail_stops and events.count("stop") == 1:
                raise RuntimeError("synthetic rollback failure")

        def wake(self) -> None:
            raise AssertionError("failed startup cannot subscribe to change wakes")

    for candidate in (
        "SourceScheduleLoop",
        "WorkerStepReadinessLoop",
        "NotificationLoop",
        "ManagerWakeLoop",
    ):
        monkeypatch.setattr(loops, candidate, Candidate)
    cfg = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(tmp_path / "synthetic.db"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "polling.lock"),
        },
    )

    async def run() -> None:
        nonlocal fail_stops
        try:
            if fail_during_reconciliation:
                # Strict preparation must preserve its startup failure even when
                # rollback fails, and keep the partial owner available for retry.
                with pytest.raises(
                    RuntimeError, match="synthetic reconciliation failure"
                ) as caught:
                    loops.prepare_background_loops(
                        cfg,
                        build_clock(cfg),
                        conversation_system=cast(ConversationSystem, object()),
                        asyncio_loop=asyncio.get_running_loop(),
                    )
                if fail_during_cleanup:
                    assert loops._active is not None
                    assert events.count("stop") == 4
                    assert "synthetic rollback failure" in caught.value.__notes__[0]
                    _assert_lock(cfg.dispatcher_lock_path, held=True)
                    fail_stops = False
                    await loops._active.stop()
                    fail_stops = True
                assert loops._active is None
                events.clear()

            def start() -> loops.BackgroundLoops:
                return loops.start_background_loops(
                    cfg,
                    build_clock(cfg),
                    conversation_system=cast(ConversationSystem, object()),
                    asyncio_loop=asyncio.get_running_loop(),
                )

            if fail_during_cleanup:
                with pytest.raises(RuntimeError, match="synthetic .* failure") as caught:
                    start()
                assert str(caught.value) == (
                    "synthetic reconciliation failure"
                    if fail_during_reconciliation
                    else "synthetic partial admission failure"
                )
                assert "synthetic rollback failure" in caught.value.__notes__[0]
                running = loops._active
                assert running is not None and not running._stopped
                assert running.source_schedule_loop is not None
            else:
                running = start()
                assert running.worker_step_readiness_loop is None
                assert running.source_schedule_loop is None
                assert running.notification_loop is None
                assert running.manager_wake_loop is None
            assert loops._active is running
            assert events.count("stop") == 4
            assert events.count("start") == (0 if fail_during_reconciliation else 3)
            _assert_lock(cfg.dispatcher_lock_path, held=fail_during_cleanup)
            with pytest.raises(RuntimeError, match="already running"):
                start()
        finally:
            fail_stops = False
            if loops._active is not None:
                await loops._active.stop()
        assert loops._active is None
        _assert_lock(cfg.dispatcher_lock_path, held=False)

    asyncio.run(run())


@pytest.mark.parametrize("identity_valid", [True, False])
def test_darwin_home_startup_polls_an_external_waiting_update(
    tmp_path: Path, cfg: object, monkeypatch: pytest.MonkeyPatch, identity_valid: bool,
) -> None:
    """The production lifespan must own the thread that advances an external attempt."""
    import json
    import os
    from dataclasses import replace

    from fastapi.testclient import TestClient
    from tests.support.update_apps import SHA_A, build_app
    from tests.unit.test_update_service import Harness

    from planner.core.config import Config
    from planner.core.server import create_app, resolve_application_root
    from planner.environments.update_attempts import atomic_write_json, attempt_to_dict, utc_now
    from planner.environments.update_contracts import UpdateAttempt
    from planner.environments.update_service import UpdateService
    from planner.platform_runtime.darwin.adapter import DarwinPlatformAdapter
    from planner.platform_runtime.darwin.contracts import MacHomeInstallation
    from planner.platform_runtime.darwin.runtime_contracts import DarwinRuntimeContract
    from planner.platform_runtime.linux.adapter import LinuxPlatformAdapter
    from planner.platform_runtime.linux.update_installer import MacService

    # Reuse the existing update fixture and ASGI host. Native process operations
    # remain separate Mac proof; this case exercises actual production loop startup.
    harness = Harness(tmp_path / "home")
    (harness.root / "workspaces").mkdir()
    build_app(harness.root / "app", SHA_A, "installed")
    harness.release = replace(harness.release, mac=None, manifest=replace(
        harness.release.manifest, runtime_contract=DarwinRuntimeContract("3.12.16", "f" * 64),
    ))
    label = "com.panels.proof.test"
    record = MacHomeInstallation(
        os.getuid(), harness.root, label, harness.root / "agents" / f"{label}.plist",
        harness.root / "agents" / f"{label}.backup.plist", Path(sys.executable),
    )
    (harness.root / "data/mac-home-installation.json").write_text(json.dumps(record.as_dict()))
    config_path = harness.root / "data/config.yaml"
    config_path.write_text(json.dumps({"provision_account_skills": False}))
    monkeypatch.setenv("PLAN_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("PLAN_APP_ROOT", str(harness.root / "app"))

    def authenticate(service: MacService, *, parent: int | None = None) -> None:
        assert service.root == harness.root
        assert parent == os.getppid()
        if not identity_valid:
            raise ValueError("The installed launchd identity is unavailable")

    # Only native launchctl/process evidence is simulated. The installation record,
    # current_service, own_service_unit, and no-repository configuration run normally.
    monkeypatch.setattr(MacService, "validate_running_home", authenticate)
    isolated = replace(
        cast(Config, cfg), db_path=str(harness.database), test_mode=False,
        dispatch_enabled=True, tick_seconds=60, app_sha=SHA_A, executor_id=None,
        dispatcher_lock_path=str(harness.root / "data/dispatcher.lock"),
        logs_dir=str(harness.root / "logs"), backup_dir=str(harness.root / "data/backups"),
        workspace_root=harness.root / "workspaces", provision_account_skills=False,
    )
    adapter = DarwinPlatformAdapter()
    if sys.platform != "darwin":
        monkeypatch.setattr(adapter, "worker_processes", LinuxPlatformAdapter().worker_processes)
    monkeypatch.setattr(
        "planner.platform_runtime.selection.get_platform_adapter", lambda *args: adapter,
    )
    monkeypatch.setattr("planner.environments.update_service.get_platform_adapter", lambda: adapter)
    monkeypatch.setattr("planner.environments.update_install.get_platform_adapter", lambda: adapter)
    monkeypatch.setattr("planner.core.server._REPO_ROOT", resolve_application_root())
    monkeypatch.delenv("PLAN_UPDATE_REPOSITORY", raising=False)
    monkeypatch.delenv("PLAN_UPDATE_TOKEN_FILE", raising=False)
    app = create_app(isolated, build_clock(isolated), lambda: connect(str(harness.database)))
    launched = threading.Event()
    with TestClient(app):
        updater = cast(UpdateService, app.state.updates)
        owner = loops._active
        assert owner is not None and owner.worker_step_readiness_loop is not None
        thread = owner.worker_step_readiness_loop._thread
        assert thread is not None and thread.is_alive()
        assert getattr(owner.worker_step_readiness_loop._before_poll, "__self__", None) is (
            updater
        )
        assert updater.provider is None
        assert updater._service_unit == (f"gui/{os.getuid()}/{label}" if identity_valid else "")
        assert updater._event_loop is not None and updater._event_loop.is_running()
        updater._launch = lambda *args: launched.set()
        deadline = monotonic() + 5
        while updater.status().activity != "idle" and monotonic() < deadline:
            threading.Event().wait(0.01)
        assert updater.status().activity == "idle"
        now = utc_now()
        # Write the other process's bytes without its process-local change signal.
        atomic_write_json(harness.store.path, attempt_to_dict(UpdateAttempt(
            attempt_id="external-native-proof", from_sha=SHA_A,
            to_sha=harness.release.manifest.app_sha, mode="idle", confirm_activity=False,
            state="waiting", created_at=now, updated_at=now, release=harness.release,
        )))
        deadline = monotonic() + 10
        while monotonic() < deadline:
            attempt = harness.store.read()
            if attempt is not None and (
                (identity_valid and launched.is_set() and attempt.backup is not None)
                or (not identity_valid and attempt.state == "failed")
            ):
                break
            threading.Event().wait(0.01)
        assert attempt is not None and "work_paused" in attempt.steps, updater.status()
        if identity_valid:
            assert launched.wait(2) and attempt.state == "installing"
            assert attempt.backup is not None
        else:
            assert attempt.state == "failed" and not launched.is_set()
            assert "launchd identity is unavailable" in (attempt.detail or "")
