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
from planner.measure_sources.contracts import SourceRuntime
from planner.measure_sources.scheduling import SourceScheduleParticipant
from planner.runtime.lock import ensure_machine_lock
from planner.scheduled_tickets.contracts import ScheduledTicketOccurrence
from planner.scheduled_tickets.participants import SlotParticipant, TicketScheduleParticipant
from planner.scheduled_tickets.registry import build_participants
from planner.scheduled_tickets.runtime import ScheduledTicketLoop


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
    """The test seam runs both production participants under the actual barrier."""
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

        def poll(
            self, conn: sqlite3.Connection, *, local_now: datetime, now: int
        ) -> list[ScheduledTicketOccurrence]:
            assert admission.active
            polls[self.position] += 1
            result = self.participant.poll(conn, local_now=local_now, now=now)
            if self.position == 1:
                source_polled.set()
            return result

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
    assert isinstance(registered[0], TicketScheduleParticipant)
    assert isinstance(registered[1], SourceScheduleParticipant)
    assert owner.worker_step_readiness_loop is None
    assert owner.notification_loop is None and owner.manager_wake_loop is None
    scheduler = owner.scheduled_ticket_loop
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
        # Closed admission blocks both participant polls, not only source execution.
        assert admission.claim(lambda: None, idle=lambda: True, require_idle=True)
        before = list(polls)
        assert scheduler.poll_once() == []
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
    scheduler = ScheduledTicketLoop(
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
        scheduled_ticket_loop=scheduler,
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
        "ScheduledTicketLoop",
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
                assert running.scheduled_ticket_loop is not None
            else:
                running = start()
                assert running.worker_step_readiness_loop is None
                assert running.scheduled_ticket_loop is None
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
