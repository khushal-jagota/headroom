"""The shared clock admits receipts without Tickets; shutdown drains its exact runs."""

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlite3 import Connection
from zoneinfo import ZoneInfo

import pytest
from tests.support.measure_sources import NOW, configured_source

from planner.core.clock import TestClock as FakeClock
from planner.core.config import load_config
from planner.core.contracts import OWNER_PRINCIPAL
from planner.credentials.contracts import CredentialResolver
from planner.measure_sources import actions, data, run_actions
from planner.measure_sources.contracts import (
    FailureCode,
    RunAdmission,
    RunFailure,
    RunMetrics,
    RunMode,
    RunResult,
    RunStatus,
    RuntimeLimits,
    SourceScheduleBody,
)
from planner.measure_sources.dispatcher import SourceDispatcher
from planner.measure_sources.schedule_loop import SourceScheduleLoop
from planner.measure_sources.scheduling import SourceScheduleParticipant


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
    loop = SourceScheduleLoop(path, fake_clock, participants=(participant,))
    before = tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
    loop.poll_once()
    loop.poll_once()
    rows = tmp_db.execute(
        "SELECT * FROM measure_source_runs WHERE source_id=?", (source.id,)
    ).fetchall()
    assert len(rows) == 1 and rows[0]["status"] == "pending" and rows[0]["trigger"] == "scheduled"
    assert tmp_db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == before
    assert not runtime.started.is_set()
    assert not tmp_db.execute("SELECT 1 FROM manager_wakes").fetchone()
    assert config.credential_key_path == str(Path(path).parent / "credential-secrets" / "key")


def test_missed_source_schedule_catches_up_only_latest_slot_once(
    tmp_db: Connection, fake_clock: FakeClock,
) -> None:
    source, _ = configured_source(tmp_db, now=NOW - 4 * 86400, local_time="12:00")
    now = datetime.fromtimestamp(NOW).astimezone().replace(hour=13, minute=0) + timedelta(days=3)
    fake_clock.set(now)
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": path})
    participant = SourceScheduleParticipant(config, fake_clock, HeldRuntime())
    for _ in range(2):
        participant.poll(tmp_db, local_now=now, now=int(now.timestamp()))
    rows = tmp_db.execute(
        "SELECT schedule_date,schedule_time FROM measure_source_runs WHERE source_id=?",
        (source.id,),
    ).fetchall()
    assert [tuple(row) for row in rows] == [(now.date().isoformat(), "12:00")]


def test_catchup_window_uses_elapsed_time_and_skips_nonexistent_local_slots() -> None:
    from planner.measure_sources.local_time import latest_due_slot

    berlin = ZoneInfo("Europe/Berlin")
    # Yesterday's 03:30 is more than 24 real hours ago across the autumn clock change.
    assert latest_due_slot("03:30", datetime(2026, 10, 25, 3, 15, tzinfo=berlin)) is None
    # A nonexistent daily slot is not manufactured from the spring-forward gap.
    assert latest_due_slot("02:30", datetime(2026, 3, 29, 3, 0, tzinfo=berlin)) == datetime(
        2026, 3, 28, 2, 30, tzinfo=berlin
    )
    now = datetime(2026, 11, 11, 11, 0, tzinfo=UTC)
    assert latest_due_slot("12:00", now) == datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def test_catchup_resolves_historical_system_offset_for_real_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from planner.measure_sources.local_time import latest_due_slot

    try:
        with monkeypatch.context() as environment:
            environment.setenv("TZ", "Europe/Berlin")
            time.tzset()
            # Like RealClock.now(), astimezone() carries only the current fixed offset.
            now = datetime(2026, 10, 25, 2, 15, tzinfo=UTC).astimezone()
            assert not isinstance(now.tzinfo, ZoneInfo)
            assert latest_due_slot("03:30", now, system_local=True) is None
            now = datetime(2026, 3, 29, 1, 0, tzinfo=UTC).astimezone()
            assert latest_due_slot("02:30", now, system_local=True) == datetime(
                2026, 3, 28, 1, 30, tzinfo=UTC
            )
    finally:
        # Restore the process timezone as well as TZ, even if an assertion fails.
        time.tzset()


def test_dispatcher_skips_old_queued_occurrence_after_clock_jump(
    tmp_db: Connection, fake_clock: FakeClock, tmp_path: Path,
) -> None:
    local_now = datetime.fromtimestamp(NOW).astimezone().replace(second=0, microsecond=0)
    source, _ = configured_source(
        tmp_db, now=NOW - 4 * 86400, local_time=local_now.strftime("%H:%M")
    )
    old = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=local_now - timedelta(days=2), now=NOW - 2 * 86400
    )
    superseded = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=local_now - timedelta(days=1), now=NOW - 86400
    )
    latest = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=local_now, now=NOW
    )
    fake_clock.set(local_now)
    runtime = HeldRuntime()
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    dispatcher = SourceDispatcher(path, fake_clock, runtime, key_path=tmp_path / "unused-key")
    dispatcher.start()
    try:
        assert runtime.started.wait(3)
        assert runtime.run_ids == [latest.id]
        assert data.read_run(tmp_db, old.id).status is RunStatus.interrupted
        assert data.read_run(tmp_db, superseded.id).status is RunStatus.interrupted
        assert not tmp_db.execute("SELECT 1 FROM measure_source_failure_episodes").fetchone()
        assert not tmp_db.execute("SELECT 1 FROM manager_wakes").fetchone()
    finally:
        dispatcher.stop(deadline=time.monotonic() + 5)


def test_scheduled_claim_rechecks_schedule_but_keeps_manual_runs(
    tmp_db: Connection,
) -> None:
    local_now = datetime.fromtimestamp(NOW).astimezone()
    source, _ = configured_source(
        tmp_db, now=NOW - 86400, local_time=local_now.strftime("%H:%M")
    )
    scheduled = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=local_now, now=NOW
    )
    assert source.active_script is not None
    manual = run_actions.request_run(
        tmp_db, source.id, mode=RunMode.collection, history=None,
        expected_script_revision=source.active_script.revision,
        principal=OWNER_PRINCIPAL, now=NOW,
    )
    actions.set_schedule(
        tmp_db, source.id,
        SourceScheduleBody(
            expected_revision=source.source_revision, enabled=False, local_time=None
        ),
        principal=OWNER_PRINCIPAL, now=NOW + 1,
    )
    assert run_actions.claim_run(tmp_db, scheduled.id, now=NOW + 1) is None
    assert data.read_run(tmp_db, scheduled.id).status is RunStatus.interrupted
    assert run_actions.claim_run(tmp_db, manual.id, now=NOW + 1) is not None
    assert not tmp_db.execute("SELECT 1 FROM measure_source_failure_episodes").fetchone()
