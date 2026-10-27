"""Source authority, atomic receipts, restart fences, and clock-sensitive status."""

from datetime import datetime
from pathlib import Path
from sqlite3 import Connection
from zoneinfo import ZoneInfo

import pytest
from tests.support.measure_sources import NOW, configured_source
from tests.unit.test_manager_wakes import _item_and_ticket

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import (
    OWNER_PRINCIPAL,
    PlannerError,
    Principal,
    PrincipalKind,
)
from planner.manager_wakes import data as wake_data
from planner.managers import data as managers
from planner.measure_sources import actions, data, run_actions, views
from planner.measure_sources.contracts import (
    FailureCode,
    RunMetrics,
    RunMode,
    RunResult,
    RunStatus,
    SourceRun,
    SourceScheduleBody,
    SourceScriptBody,
)
from planner.measures import data as measure_data
from planner.measures.contracts import CandidateObservation, SourceHealth

METRICS = RunMetrics(0, 0, 0, 1, 0, 0)


def _run(
    conn: Connection, source_id: str, *, mode: RunMode = RunMode.collection, now: int = NOW
) -> SourceRun:
    source = data.read_source(conn, source_id)
    script = source.active_script if mode is RunMode.collection else source.pending_script
    assert script is not None
    run = run_actions.request_run(
        conn,
        source_id,
        mode=mode,
        history=None,
        expected_script_revision=script.revision,
        principal=OWNER_PRINCIPAL,
        now=now,
    )
    assert run_actions.claim_run(conn, run.id, now=now) is not None
    return run


def test_designated_worker_can_only_edit_and_test_pending_revision(
    tmp_db: Connection,
    fake_clock: FakeClock,
) -> None:
    manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    manager = Principal(PrincipalKind.manager, manager_id)
    worker = Principal(PrincipalKind.ticket, ticket_id)
    source, measure = configured_source(tmp_db, responsible=manager)
    with pytest.raises(PlannerError):
        actions.set_schedule(
            tmp_db,
            source.id,
            SourceScheduleBody(
                expected_revision=source.source_revision,
                enabled=False,
                local_time="12:00",
            ),
            principal=manager,
            now=NOW,
        )
    body = SourceScriptBody(
        expected_revision=source.source_revision,
        expected_pending_revision=None,
        text="pass # repair",
    )
    with pytest.raises(PlannerError):
        actions.submit_script(tmp_db, source.id, body, principal=worker, now=NOW)
    actions.assign_repair_ticket(tmp_db, source.id, ticket_id, principal=manager, now=NOW)
    source = actions.submit_script(tmp_db, source.id, body, principal=worker, now=NOW)
    assert source.pending_script is not None
    with pytest.raises(PlannerError):
        actions.publish_script(
            tmp_db,
            source.id,
            source.pending_script.revision,
            expected_revision=source.source_revision,
            principal=worker,
            now=NOW,
        )
    run = run_actions.request_run(
        tmp_db,
        source.id,
        mode=RunMode.test,
        history=None,
        expected_script_revision=source.pending_script.revision,
        principal=worker,
        now=NOW,
    )
    assert run_actions.claim_run(tmp_db, run.id, now=NOW)
    candidate = CandidateObservation(measure.id, "2026-10-27", 120)
    result = run_actions.complete_run(
        tmp_db, run.id, RunResult((candidate,), None, METRICS), now=NOW
    )
    assert result.run.status is RunStatus.succeeded and result.test_candidates == (candidate,)
    assert measure_data.latest_observations(tmp_db, (measure.id,)) == ()
    # Manager project classification does not change explicit source responsibility or scope.
    managers.update_manager(
        tmp_db, manager_id, edits={"project_id": "project_other"}, clock=fake_clock
    )
    unchanged = data.read_source(tmp_db, source.id)
    assert unchanged.responsible == manager and unchanged.scope.project_id == "project_vylo"
    with pytest.raises(PlannerError, match="reassign"):
        managers._require_manager_can_delete(tmp_db, manager_id)


def test_atomic_collection_failure_dedup_and_success_recovery(tmp_db: Connection) -> None:
    source, measure = configured_source(tmp_db)
    run = _run(tmp_db, source.id)
    candidate = CandidateObservation(measure.id, "2026-10-27", 100)
    assert (
        run_actions.complete_run(
            tmp_db, run.id, RunResult((candidate,), None, METRICS), now=NOW
        ).run.status
        is RunStatus.succeeded
    )
    for index in range(2):
        failed = _run(tmp_db, source.id, now=NOW + index + 1)
        result = RunResult(
            (
                CandidateObservation(measure.id, "2026-10-27", 999),
                CandidateObservation("foreign", "2026-10-27", 2),
            ),
            None,
            METRICS,
        )
        failure = run_actions.complete_run(tmp_db, failed.id, result, now=NOW + 3).run.failure
        assert failure is not None and failure.code is FailureCode.invalid_output
    assert measure_data.latest_observations(tmp_db, (measure.id,))[0].value == 100
    assert (
        tmp_db.execute(
            "SELECT COUNT(*) FROM manager_wakes WHERE source_kind='source_failure'"
        ).fetchone()[0]
        == 1
    )
    assert data.read_health(tmp_db, (source.id,), now=NOW + 4)[0].health is SourceHealth.failed
    actions.submit_script(
        tmp_db,
        source.id,
        SourceScriptBody(
            expected_revision=source.source_revision,
            expected_pending_revision=None,
            text="pass # test",
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW + 4,
    )
    test = _run(tmp_db, source.id, mode=RunMode.test, now=NOW + 4)
    run_actions.complete_run(tmp_db, test.id, RunResult((candidate,), None, METRICS), now=NOW + 4)
    status = views.status(tmp_db, source.id, now=NOW + 4)
    assert status.latest_run is not None and status.latest_run.status is RunStatus.succeeded
    assert (
        status.collection_failure is not None
        and status.collection_failure.code is FailureCode.invalid_output
    )
    assert status.health is SourceHealth.failed
    success = _run(tmp_db, source.id, now=NOW + 5)
    run_actions.complete_run(
        tmp_db, success.id, RunResult((candidate,), None, METRICS), now=NOW + 5
    )
    assert data.read_failure_episode(tmp_db, source.id) is None
    assert wake_data.claim_next_batch(tmp_db, process_token="recovered", now=NOW + 6) is None
    summary = views.list_json(tmp_db, project_id=None, limit=10, offset=0, now=NOW + 6)
    assert "active_script" not in summary["sources"][0]["source"] and "text" not in str(summary)
    assert summary["sources"][0]["latest_values"][0]["name"] == measure.name


def test_pending_restart_and_late_completion_fence(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, measure = configured_source(tmp_db)
    identifiers = iter(("source_run_zzz", "source_run_aaa"))
    monkeypatch.setattr(run_actions, "new_id", lambda _prefix: next(identifiers))
    run = _run(tmp_db, source.id)
    assert source.active_script is not None
    pending = run_actions.request_run(
        tmp_db,
        source.id,
        mode=RunMode.collection,
        history=None,
        expected_script_revision=source.active_script.revision,
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    # Equal-second receipts use insertion order; random lexical IDs are not chronology.
    status = views.status(tmp_db, source.id, now=NOW)
    assert status.latest_run is not None and status.latest_run.id == pending.id
    assert status.latest_run.status is RunStatus.pending
    history = views.runs_json(tmp_db, source.id, limit=10, offset=0)
    assert [item["id"] for item in history["runs"]] == [pending.id, run.id]
    assert run_actions.claim_run(tmp_db, pending.id, now=NOW) is None
    assert run_actions.interrupt_abandoned_runs(tmp_db, now=NOW + 1) == 1
    assert data.read_run(tmp_db, pending.id).status is RunStatus.pending
    with pytest.raises(PlannerError, match="only a running"):
        run_actions.complete_run(
            tmp_db,
            run.id,
            RunResult((CandidateObservation(measure.id, "2026-10-27", 7),), None, METRICS),
            now=NOW + 2,
        )
    assert measure_data.latest_observations(tmp_db, (measure.id,)) == ()
    assert run_actions.claim_run(tmp_db, pending.id, now=NOW + 2) is not None


def test_shared_slot_dedup_and_dst_health_entrypoint(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TZ", "Europe/Berlin")
    zone = ZoneInfo("Europe/Berlin")
    created = int(datetime(2026, 3, 28, 0, tzinfo=zone).timestamp())
    source, _ = configured_source(tmp_db, now=created, local_time="02:30")
    spring_now = int(datetime(2026, 3, 29, 12, tzinfo=zone).timestamp())
    health = data.read_health(tmp_db, (source.id,), now=spring_now)[0]
    assert health.next_status_change_at == int(
        datetime(2026, 3, 30, 2, 31, 30, tzinfo=zone).timestamp()
    )
    # The repeated autumn local minute has one durable receipt across both folds.
    first = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
    second = first.replace(fold=1)
    one = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=first, now=int(first.timestamp())
    )
    two = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=second, now=int(second.timestamp())
    )
    assert one.id == two.id
    repeated = data.read_health(tmp_db, (source.id,), now=int(second.timestamp()) + 90)[0]
    assert repeated.next_status_change_at == int(
        datetime(2026, 10, 26, 2, 31, 30, tzinfo=zone).timestamp()
    )


def test_capability_revocation_fences_admitted_results(tmp_db: Connection, tmp_path: Path) -> None:
    from dataclasses import replace

    from tests.unit.test_credentials_store import write

    from planner.credentials import actions as credentials
    from planner.measure_sources.contracts import DestinationGrant, HttpMethod, PathMatch

    source, measure = configured_source(tmp_db)
    key = tmp_path / "private-key"
    credential = credentials.create_credential(
        tmp_db,
        replace(write(), scope=source.scope),
        principal=OWNER_PRINCIPAL,
        now=NOW,
        key_path=key,
    )
    source = actions.set_destinations(
        tmp_db,
        source.id,
        (
            DestinationGrant(
                "example-read-key",
                "https://example.com",
                HttpMethod.GET,
                "/query",
                PathMatch.exact,
                credential.reference,
            ),
        ),
        expected_revision=source.source_revision,
        principal=OWNER_PRINCIPAL,
        now=NOW,
    )
    run = _run(tmp_db, source.id)
    credentials.update_origins(
        tmp_db,
        credential.id,
        ("https://other.example",),
        expected_revision=credential.revision,
        principal=OWNER_PRINCIPAL,
        now=NOW + 1,
        key_path=key,
    )
    result = run_actions.complete_run(
        tmp_db,
        run.id,
        RunResult((CandidateObservation(measure.id, "2026-10-27", 100),), None, METRICS),
        now=NOW + 2,
    )
    assert (
        result.run.failure is not None and result.run.failure.code is FailureCode.stale_definition
    )
    assert measure_data.latest_observations(tmp_db, (measure.id,)) == ()
