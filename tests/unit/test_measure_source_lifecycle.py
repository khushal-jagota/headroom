"""Source authority, atomic receipts, restart fences, and actual-outcome status."""

from datetime import datetime
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
    SourceCreate,
    SourceRun,
    SourceScheduleBody,
    SourceScriptBody,
)
from planner.measures import actions as measure_actions
from planner.measures import data as measure_data
from planner.measures.contracts import CandidateObservation, SourceHealth

METRICS = RunMetrics(0, 0, 0, 1, 0)


def _run(
    conn: Connection, source_id: str, *, mode: RunMode = RunMode.collection, now: int = NOW
) -> SourceRun:
    source = data.read_source(conn, source_id)
    script = source.active_script
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


def test_assigned_worker_saves_tests_and_collects_without_daily_schedule(
    tmp_db: Connection,
    fake_clock: FakeClock,
) -> None:
    manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    manager = Principal(PrincipalKind.manager, manager_id)
    worker = Principal(PrincipalKind.ticket, ticket_id)
    source, measure = configured_source(
        tmp_db, responsible=manager, enabled=False, principal=worker
    )
    source = actions.set_schedule(
        tmp_db,
        source.id,
        SourceScheduleBody(
            expected_revision=source.source_revision, enabled=False, local_time="12:00"
        ),
        principal=worker,
        now=NOW,
    )
    body = SourceScriptBody(expected_revision=source.source_revision, text="pass # repair")
    source = actions.submit_script(tmp_db, source.id, body, principal=worker, now=NOW)
    assert source.active_script is not None and source.active_script.text == body["text"]
    with pytest.raises(PlannerError, match="revision changed"):
        actions.submit_script(tmp_db, source.id, body, principal=worker, now=NOW)
    other_manager, _ = _item_and_ticket(tmp_db, fake_clock)
    with pytest.raises(PlannerError):
        actions.submit_script(
            tmp_db,
            source.id,
            SourceScriptBody(expected_revision=source.source_revision, text="pass"),
            principal=Principal(PrincipalKind.manager, other_manager),
            now=NOW,
        )
    candidate = CandidateObservation(
        measure.id,
        "2026-10-27",
        120,
        query_start_at=float(NOW - 3600),
        query_end_at=float(NOW),
        limitation="Provider sample",
    )
    for mode in (RunMode.test, RunMode.collection):
        run = run_actions.request_run(
            tmp_db,
            source.id,
            mode=mode,
            history=None,
            expected_script_revision=source.active_script.revision,
            principal=worker,
            now=NOW,
        )
        assert run_actions.claim_run(tmp_db, run.id, now=NOW)
        result = run_actions.complete_run(
            tmp_db, run.id, RunResult((candidate,), None, METRICS), now=NOW
        )
        assert result.run.status is RunStatus.succeeded
        if mode is RunMode.test:
            assert result.test_candidates == (candidate,)
            assert measure_data.latest_observations(tmp_db, (measure.id,)) == ()
            assert views.status(tmp_db, source.id, now=NOW).health is SourceHealth.disabled
        else:
            assert measure_data.latest_observations(tmp_db, (measure.id,))[0].value == 120
            retained = measure_data.latest_observations(tmp_db, (measure.id,))[0]
            assert (retained.query_start_at, retained.query_end_at, retained.limitation) == (
                candidate.query_start_at,
                candidate.query_end_at,
                candidate.limitation,
            )
            assert views.status(tmp_db, source.id, now=NOW).health is SourceHealth.healthy
    with pytest.raises(PlannerError, match="retained"):
        actions.delete_source(tmp_db, source.id, principal=worker)
    disposable = actions.create_source(
        tmp_db, SourceCreate("Optional test", source.scope, manager), principal=worker, now=NOW
    )
    disposable = actions.submit_script(
        tmp_db,
        disposable.id,
        SourceScriptBody(expected_revision=disposable.source_revision, text="pass"),
        principal=worker,
        now=NOW,
    )
    test_run = _run(tmp_db, disposable.id, mode=RunMode.test)
    assert (
        run_actions.complete_run(
            tmp_db, test_run.id, RunResult((), None, METRICS), now=NOW
        ).run.status
        is RunStatus.succeeded
    )
    actions.delete_source(tmp_db, disposable.id, principal=worker)
    assert (
        tmp_db.execute("SELECT 1 FROM measure_source_runs WHERE id=?", (test_run.id,)).fetchone()
        is None
    )
    with pytest.raises(PlannerError, match="does not exist"):
        data.read_source(tmp_db, disposable.id)
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


def test_shared_daily_slot_deduplicates_repeated_dst_minute(tmp_db: Connection) -> None:
    zone = ZoneInfo("Europe/Berlin")
    source, _ = configured_source(tmp_db, local_time="02:30")
    first = datetime(2026, 10, 25, 2, 30, tzinfo=zone, fold=0)
    second = first.replace(fold=1)
    one = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=first, now=int(first.timestamp())
    )
    two = run_actions.admit_scheduled_run(
        tmp_db, source.id, local_now=second, now=int(second.timestamp())
    )
    assert one.id == two.id
    assert (
        data.read_health(tmp_db, (source.id,), now=NOW + 86400)[0].health
        is SourceHealth.never_collected
    )


def test_script_change_fences_results_but_schedule_or_label_change_does_not(
    tmp_db: Connection,
) -> None:
    source, measure = configured_source(tmp_db)
    run = _run(tmp_db, source.id)
    source = actions.set_schedule(
        tmp_db,
        source.id,
        SourceScheduleBody(
            expected_revision=source.source_revision, enabled=False, local_time="12:00"
        ),
        principal=OWNER_PRINCIPAL,
        now=NOW + 1,
    )
    measure_actions.update_measure(
        tmp_db,
        measure.id,
        {"expected_revision": measure.definition_revision, "name": "Renamed reading"},
        principal=OWNER_PRINCIPAL,
        now=NOW + 1,
    )
    source = data.read_source(tmp_db, source.id)
    candidate = CandidateObservation(measure.id, "2026-10-27", 100)
    result = run_actions.complete_run(
        tmp_db, run.id, RunResult((candidate,), None, METRICS), now=NOW + 2
    )
    assert result.run.status is RunStatus.succeeded
    run = _run(tmp_db, source.id)
    actions.submit_script(
        tmp_db,
        source.id,
        SourceScriptBody(expected_revision=source.source_revision, text="pass # changed"),
        principal=OWNER_PRINCIPAL,
        now=NOW + 3,
    )
    result = run_actions.complete_run(
        tmp_db,
        run.id,
        RunResult((CandidateObservation(measure.id, "2026-10-27", 200),), None, METRICS),
        now=NOW + 4,
    )
    assert (
        result.run.failure is not None and result.run.failure.code is FailureCode.stale_definition
    )
    assert measure_data.latest_observations(tmp_db, (measure.id,))[0].value == 100
    assert views.status(tmp_db, source.id, now=NOW + 4).health is SourceHealth.failed


def test_mixed_dataset_receipts_validation_test_isolation_and_captured_schema(
    tmp_db: Connection,
) -> None:
    from dataclasses import replace

    from tests.unit.test_dataset_persistence import candidate, dataset, request

    from planner.datasets import actions as datasets
    from planner.datasets import data as dataset_data
    from planner.datasets.contracts import DatasetDimension, DatasetField, DatasetFieldKind

    source, measure = configured_source(tmp_db)
    table = dataset(tmp_db, source.id)
    original = replace(candidate(table, "2026-10-25", "de"), labels={"region": {"de": "Germany"}})
    scalar = CandidateObservation(measure.id, "2026-10-25", 3)
    first = _run(tmp_db, source.id)
    completed = run_actions.complete_run(
        tmp_db, first.id, RunResult((scalar,), None, METRICS, (original,)), now=NOW + 1
    )
    assert (
        completed.run.observation_count,
        completed.run.dataset_result_count,
        completed.run.dataset_row_count,
    ) == (1, 1, 1)
    retained = dataset_data.read_values(tmp_db, request(table), now=NOW)
    assert retained.results[0].output == first.dataset_outputs[0]
    status = views.status(tmp_db, source.id, now=NOW)
    assert status.dataset_output_count == 1
    assert status.latest_run is not None and status.latest_run.dataset_row_count == 1
    replacement = replace(
        candidate(table, "2026-10-25", "us"), labels={"region": {"de": "Renamed"}}
    )
    test_run = _run(tmp_db, source.id, mode=RunMode.test)
    tested = run_actions.complete_run(
        tmp_db,
        test_run.id,
        RunResult((replace(scalar, value=9),), None, METRICS, (replacement,)),
        now=NOW + 2,
    )
    assert tested.test_dataset_results == (replacement,)
    assert tested.run.dataset_result_count == tested.run.dataset_row_count == 0
    assert dataset_data.read_values(tmp_db, request(table), now=NOW) == retained
    assert measure_data.latest_observations(tmp_db, (measure.id,))[0].value == 3
    for rejected in ((replacement, replacement), (replace(replacement, dataset_id="foreign"),)):
        pending = _run(tmp_db, source.id)
        failed = run_actions.complete_run(
            tmp_db,
            pending.id,
            RunResult((replace(scalar, value=99),), None, METRICS, rejected),
            now=NOW + 3,
        )
        assert (
            failed.run.failure is not None and failed.run.failure.code is FailureCode.invalid_output
        )
        assert failed.run.dataset_result_count == failed.run.dataset_row_count == 0
        # Health changes, but the retained headers, rows, and labels do not.
        current = dataset_data.read_values(tmp_db, request(table), now=NOW)
        assert current.results == retained.results and current.labels == retained.labels
        assert measure_data.latest_observations(tmp_db, (measure.id,))[0].value == 3
    captured = _run(tmp_db, source.id)
    datasets.update_dataset(
        tmp_db,
        table.id,
        {
            "expected_revision": 1,
            "dimensions": (DatasetDimension("new_region", "New region"),),
            "fields": (DatasetField("new_events", "New events", "events", DatasetFieldKind.count),),
        },
        principal=OWNER_PRINCIPAL,
        now=NOW + 4,
    )
    result = run_actions.complete_run(
        tmp_db,
        captured.id,
        RunResult(
            (),
            None,
            METRICS,
            (candidate(table, "2026-10-25"), replace(original, date="2026-10-27")),
        ),
        now=NOW + 5,
    )
    assert result.run.status is RunStatus.succeeded
    assert (result.run.dataset_result_count, result.run.dataset_row_count) == (2, 1)
    corrected = dataset_data.read_values(tmp_db, request(table), now=NOW)
    assert corrected.results[0].rows == ()
    assert all(value.output == captured.dataset_outputs[0] for value in corrected.results)
    assert all(
        value.provenance.source_revision == captured.source_revision
        and value.provenance.script_sha256 == captured.script_sha256
        and value.provenance.collected_at == NOW + 5
        for value in corrected.results
    )
