"""The shared clock admits receipts without Tickets; shutdown drains its exact runs."""

import threading
import time
from pathlib import Path
from sqlite3 import Connection

from tests.support.measure_sources import NOW, configured_source

from planner.core.clock import TestClock as FakeClock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL
from planner.credentials.contracts import CredentialResolver
from planner.measure_sources import data, run_actions
from planner.measure_sources.contracts import (
    FailureCode,
    RunAdmission,
    RunFailure,
    RunMetrics,
    RunMode,
    RunResult,
    RunStatus,
    RuntimeLimits,
)
from planner.measure_sources.dispatcher import SourceDispatcher
from planner.measure_sources.scheduling import SourceScheduleParticipant
from planner.scheduled_tickets.participants import TicketScheduleParticipant
from planner.scheduled_tickets.runtime import ScheduledTicketLoop


class HeldRuntime:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.run_ids: list[str] = []
        self.cancelled_ids: list[str] = []
        self.timeouts: list[float] = []

    def execute(self, admission: RunAdmission, credentials: CredentialResolver) -> RunResult:
        self.run_ids.append(admission.run.id)
        self.timeouts.append(admission.limits.wall_seconds)
        self.started.set()
        assert self.cancelled.wait(5)
        return RunResult((), RunFailure(FailureCode.interrupted), RunMetrics(0, 0, 0, 1, 0))

    def cancel(self, run_id: str) -> None:
        self.cancelled_ids.append(run_id)
        self.cancelled.set()


def test_dispatcher_shutdown_interrupts_exact_owned_run(
    tmp_db: Connection, fake_clock: FakeClock, tmp_path: Path
) -> None:
    source, measure = configured_source(tmp_db)
    assert source.active_script is not None
    run = run_actions.request_run(
        tmp_db,
        source.id,
        mode=RunMode.collection,
        history=None,
        expected_script_revision=source.active_script.revision,
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    runtime = HeldRuntime()
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    dispatcher = SourceDispatcher(
        path,
        fake_clock,
        runtime,
        key_path=tmp_path / "no-key-needed",
        runtime_limits=RuntimeLimits(90),
    )
    dispatcher.start()
    try:
        assert runtime.started.wait(3)
    finally:
        dispatcher.stop(deadline=time.monotonic() + 5)
    assert runtime.run_ids == [run.id] and runtime.cancelled_ids == [run.id]
    assert runtime.timeouts == [90]
    assert data.read_run(tmp_db, run.id).status is RunStatus.interrupted
    assert not tmp_db.execute(
        "SELECT 1 FROM measure_observations WHERE measure_id=?", (measure.id,)
    ).fetchone()
    assert not any(thread.name.startswith("measure-source") for thread in threading.enumerate())


def test_shared_clock_source_slot_creates_no_ticket_or_model_work(
    tmp_db: Connection, fake_clock: FakeClock, tmp_path: Path
) -> None:
    source, _ = configured_source(
        tmp_db, now=fake_clock.now_unix(), local_time=fake_clock.now().strftime("%H:%M")
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": path})
    runtime = HeldRuntime()
    participant = SourceScheduleParticipant(config, fake_clock, runtime)
    loop = ScheduledTicketLoop(
        path, fake_clock, participants=(TicketScheduleParticipant(), participant)
    )
    before = tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    assert loop.poll_once() == [] and loop.poll_once() == []
    rows = tmp_db.execute(
        "SELECT * FROM measure_source_runs WHERE source_id=?", (source.id,)
    ).fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "pending" and rows[0]["trigger"] == "scheduled"
    assert tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == before
    assert not runtime.started.is_set()
    assert not tmp_db.execute("SELECT 1 FROM manager_wakes").fetchone()
    assert config.credential_key_path == str(Path(path).parent / "credential-secrets" / "key")
