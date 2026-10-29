"""Durable run admission and the single atomic source completion door."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime

from planner.core.contracts import Principal
from planner.core.errors import ErrorCode, PlannerError
from planner.core.ids import new_id
from planner.datasets import data as datasets_data
from planner.measure_sources import data
from planner.measure_sources.authority import require_author
from planner.measure_sources.contracts import (
    FailureCode,
    MeasureSource,
    RunFailure,
    RunMode,
    RunResult,
    RunStatus,
    RunTrigger,
    SourceRun,
    SourceRunDetail,
)
from planner.measure_sources.notices import record_failure
from planner.measure_sources.records import (
    encode_candidates,
    encode_dataset_outputs,
    encode_dataset_results,
    encode_outputs,
)
from planner.measure_sources.transaction import transaction
from planner.measures import data as measures_data
from planner.measures.contracts import HistoryWindow
from planner.measures.logic.dates import calendar_date


def _selected_script(source: MeasureSource) -> tuple[int, str]:
    script = source.active_script
    if script is None:
        raise PlannerError(ErrorCode.validation, "the selected source script is absent")
    return script.revision, script.sha256


def _admit(
    conn: sqlite3.Connection,
    source: MeasureSource,
    *,
    mode: RunMode,
    trigger: RunTrigger,
    history: HistoryWindow | None,
    now: int,
    slot: tuple[str, str] | None = None,
) -> SourceRun:
    if history is not None:
        if calendar_date(history.first_date) > calendar_date(history.last_date):
            raise PlannerError(ErrorCode.validation, "history starts after its end date")
    revision, sha256 = _selected_script(source)
    outputs = measures_data.prepare_outputs(conn, source.id, frozen_at=now)
    dataset_outputs = datasets_data.prepare_outputs(conn, source.id, frozen_at=now)
    run_id = new_id("source_run")
    conn.execute(
        "INSERT INTO measure_source_runs(id,source_id,mode,trigger,status,source_revision,"
        "script_revision,script_sha256,frozen_at,required_outputs,history_first_date,"
        "history_last_date,schedule_date,schedule_time,created_at,dataset_outputs) "
        "VALUES (?,?,?,?,'pending',?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            source.id,
            mode.value,
            trigger.value,
            source.source_revision,
            revision,
            sha256,
            now,
            encode_outputs(outputs),
            None if history is None else history.first_date,
            None if history is None else history.last_date,
            None if slot is None else slot[0],
            None if slot is None else slot[1],
            now,
            encode_dataset_outputs(dataset_outputs),
        ),
    )
    return data.read_run(conn, run_id)


def request_run(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    mode: RunMode,
    history: HistoryWindow | None,
    expected_script_revision: int,
    principal: Principal,
    now: int,
) -> SourceRun:
    with transaction(conn):
        source = data.read_source(conn, source_id)
        require_author(conn, principal, source)
        revision, _ = _selected_script(source)
        if revision != expected_script_revision:
            raise PlannerError(ErrorCode.stale_claim, "selected source script changed")
        return _admit(
            conn,
            source,
            mode=mode,
            trigger=RunTrigger.test if mode is RunMode.test else RunTrigger.manual,
            history=history,
            now=now,
        )


def admit_scheduled_run(
    conn: sqlite3.Connection, source_id: str, *, local_now: datetime, now: int
) -> SourceRun:
    with transaction(conn):
        source = data.read_source(conn, source_id)
        slot = (local_now.date().isoformat(), local_now.strftime("%H:%M"))
        if not source.enabled or source.local_time != slot[1]:
            raise PlannerError(ErrorCode.validation, "source is not due in this slot")
        existing = conn.execute(
            "SELECT id FROM measure_source_runs WHERE source_id=? AND trigger='scheduled' "
            "AND schedule_date=? AND schedule_time=?",
            (source_id, *slot),
        ).fetchone()
        if existing is not None:
            return data.read_run(conn, str(existing[0]))
        return _admit(
            conn,
            source,
            mode=RunMode.collection,
            trigger=RunTrigger.scheduled,
            history=None,
            now=now,
            slot=slot,
        )


def current_definition(conn: sqlite3.Connection, run: SourceRun) -> bool:
    try:
        source = data.read_source(conn, run.source_id)
        revision, sha256 = _selected_script(source)
    except PlannerError:
        return False
    return (revision, sha256) == (run.script_revision, run.script_sha256)


def _fail(
    conn: sqlite3.Connection,
    run: SourceRun,
    failure: RunFailure,
    *,
    now: int,
    status: RunStatus = RunStatus.failed,
) -> None:
    conn.execute(
        "UPDATE measure_source_runs SET status=?,failure=?,finished_at=? WHERE id=?",
        (status.value, json.dumps(asdict(failure)), now, run.id),
    )
    if run.mode is RunMode.collection:
        record_failure(conn, data.read_run(conn, run.id), now=now)


def claim_run(conn: sqlite3.Connection, run_id: str, *, now: int) -> SourceRun | None:
    with transaction(conn):
        run = data.read_run(conn, run_id)
        if run.status is not RunStatus.pending:
            return None
        if (
            conn.execute(
                "SELECT 1 FROM measure_source_runs WHERE source_id=? AND status='running'",
                (run.source_id,),
            ).fetchone()
            is not None
        ):
            return None
        if not current_definition(conn, run):
            _fail(conn, run, RunFailure(FailureCode.stale_definition), now=now)
            return None
        conn.execute(
            "UPDATE measure_source_runs SET status='running',started_at=? WHERE id=?", (now, run_id)
        )
        return data.read_run(conn, run_id)


def _validation_failure(error: PlannerError) -> RunFailure:
    if error.message == "stale measure definition":
        return RunFailure(FailureCode.stale_definition)
    return RunFailure(FailureCode.invalid_output)


def complete_run(
    conn: sqlite3.Connection, run_id: str, result: RunResult, *, now: int
) -> SourceRunDetail:
    with transaction(conn):
        run = data.read_run(conn, run_id)
        if run.status is not RunStatus.running:
            raise PlannerError(ErrorCode.stale_claim, "only a running source run can complete")
        failure = result.failure
        encoded: str | None = None
        encoded_datasets: str | None = None
        count = 0
        dataset_count = row_count = 0
        if failure is None and not current_definition(conn, run):
            failure = RunFailure(FailureCode.stale_definition)
        if failure is None:
            try:
                encoded = encode_candidates(result.observations)
                encoded_datasets = encode_dataset_results(result.dataset_results)
                # Validation runs first for both modes. The commit function also validates
                # as its own transaction-owned contract, before it performs any write.
                measures_data.validate_batch(
                    conn,
                    source_id=run.source_id,
                    frozen_at=run.frozen_at,
                    outputs=run.required_outputs,
                    candidates=result.observations,
                    history=run.history,
                )
                datasets_data.validate_batch(
                    conn,
                    source_id=run.source_id,
                    outputs=run.dataset_outputs,
                    candidates=result.dataset_results,
                )
            except (ValueError, TypeError):
                failure = RunFailure(FailureCode.invalid_output)
            except PlannerError as error:
                failure = _validation_failure(error)
        if failure is not None:
            _fail(
                conn,
                run,
                failure,
                now=now,
                status=RunStatus.interrupted
                if failure.code is FailureCode.interrupted
                else RunStatus.failed,
            )
        else:
            if run.mode is RunMode.collection:
                count = measures_data.commit_observations(
                    conn,
                    run_id=run.id,
                    source_id=run.source_id,
                    frozen_at=run.frozen_at,
                    outputs=run.required_outputs,
                    candidates=result.observations,
                    history=run.history,
                )
                row_count = datasets_data.commit_results(
                    conn,
                    run_id=run.id,
                    source_id=run.source_id,
                    outputs=run.dataset_outputs,
                    candidates=result.dataset_results,
                    collected_at=now,
                )
                dataset_count = len(result.dataset_results)
                conn.execute(
                    "UPDATE measure_source_failure_episodes SET closed_at=? "
                    "WHERE source_id=? AND closed_at IS NULL",
                    (now, run.source_id),
                )
            conn.execute(
                "UPDATE measure_source_runs SET status='succeeded',finished_at=?,"
                "observation_count=?,dataset_result_count=?,dataset_row_count=?,"
                "test_candidates=?,test_dataset_results=? WHERE id=?",
                (
                    now,
                    count,
                    dataset_count,
                    row_count,
                    encoded if run.mode is RunMode.test else None,
                    encoded_datasets if run.mode is RunMode.test else None,
                    run.id,
                ),
            )
        conn.execute(
            "UPDATE measure_source_runs SET metrics=? WHERE id=?",
            (json.dumps(asdict(result.metrics), allow_nan=False), run.id),
        )
        return data.read_run_detail(conn, run_id)


def interrupt_abandoned_runs(conn: sqlite3.Connection, *, now: int) -> int:
    """Only the newly acquired polling-lock owner calls this startup reconciliation."""
    with transaction(conn):
        ids = [
            str(row[0])
            for row in conn.execute("SELECT id FROM measure_source_runs WHERE status='running'")
        ]
        for run_id in ids:
            _fail(
                conn,
                data.read_run(conn, run_id),
                RunFailure(FailureCode.interrupted),
                now=now,
                status=RunStatus.interrupted,
            )
        return len(ids)
