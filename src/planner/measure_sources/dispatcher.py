"""Bounded pending-run dispatcher; the daily scheduler only admits occurrences."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from time import monotonic

from planner.core import change_signal
from planner.core.clock import Clock
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.measure_sources.admission import prepare_admission
from planner.measure_sources.configuration import DEFAULT_LIMITS
from planner.measure_sources.contracts import (
    FailureCode,
    RunFailure,
    RunMetrics,
    RunResult,
    RuntimeLimits,
    SourceRun,
    SourceRuntime,
)
from planner.measure_sources.run_actions import claim_run, complete_run, interrupt_abandoned_runs
from planner.measure_sources.settings import MAX_CONCURRENT_RUNS

_LOG = logging.getLogger(__name__)


class SourceDispatcher:
    def __init__(
        self,
        db_path: str,
        clock: Clock,
        runtime: SourceRuntime,
        *,
        key_path: Path,
        busy_timeout_ms: int = 5000,
        runtime_limits: RuntimeLimits = DEFAULT_LIMITS,
    ) -> None:
        self._db_path = db_path
        self._clock = clock
        self._runtime = runtime
        self._key_path = key_path
        self._busy_timeout_ms = busy_timeout_ms
        self._runtime_limits = runtime_limits
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._futures: dict[str, Future[None]] = {}
        self._unsubscribe: Callable[[], None] | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("source dispatcher already started")
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            interrupt_abandoned_runs(conn, now=self._clock.now_unix())
        finally:
            conn.close()
        self._executor = ThreadPoolExecutor(
            max_workers=MAX_CONCURRENT_RUNS, thread_name_prefix="measure-source"
        )
        self._unsubscribe = change_signal.subscribe(self._wake.set)
        self._thread = threading.Thread(
            target=self._loop, name="measure-source-dispatch", daemon=True
        )
        self._thread.start()

    def _execute(self, run: SourceRun) -> None:
        result: RunResult
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            try:
                admission, credentials = prepare_admission(
                    conn, run, key_path=self._key_path, limits=self._runtime_limits
                )
            except PlannerError as error:
                code = (
                    FailureCode.stale_definition
                    if error.code is ErrorCode.stale_claim
                    else FailureCode.credential_unavailable
                )
                result = RunResult((), RunFailure(code), RunMetrics(0, 0, 0, 0, 0))
            else:
                if self._stop.is_set():
                    self._runtime.cancel(run.id)
                result = self._runtime.execute(admission, credentials)
        except Exception:
            # Raw runtime exceptions are neither persisted nor logged.
            result = RunResult(
                (), RunFailure(FailureCode.runtime_failure), RunMetrics(0, 0, 0, 0, 0)
            )
        finally:
            conn.close()
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            complete_run(conn, run.id, result, now=self._clock.now_unix())
        except PlannerError as error:
            if error.code is not ErrorCode.stale_claim:
                _LOG.error(
                    "source completion refused", extra={"run_id": run.id, "code": error.code.value}
                )
        finally:
            conn.close()
            self._wake.set()

    def _pump(self) -> None:
        self._futures = {key: value for key, value in self._futures.items() if not value.done()}
        remaining = MAX_CONCURRENT_RUNS - len(self._futures)
        if remaining <= 0 or self._stop.is_set():
            return
        conn = connect(self._db_path, self._busy_timeout_ms)
        try:
            rows = conn.execute(
                "SELECT p.id FROM measure_source_runs p WHERE p.status='pending' AND NOT EXISTS "
                "(SELECT 1 FROM measure_source_runs r WHERE "
                "r.source_id=p.source_id AND r.status='running') "
                "ORDER BY p.created_at,p.rowid LIMIT ?",
                (remaining,),
            ).fetchall()
            for row in rows:
                if self._stop.is_set():
                    break
                run = claim_run(conn, str(row[0]), now=self._clock.now_unix())
                if run is not None:
                    assert self._executor is not None
                    self._futures[run.id] = self._executor.submit(self._execute, run)
        finally:
            conn.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            try:
                self._pump()
            except Exception:
                _LOG.error("source dispatcher poll failed")
            self._wake.wait(5.0)

    def stop(self, *, deadline: float | None = None) -> None:
        self._stop.set()
        self._wake.set()
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        limit = monotonic() + 30.0 if deadline is None else deadline
        thread = self._thread
        if thread is not None:
            thread.join(max(0.0, limit - monotonic()))
            if thread.is_alive():
                raise RuntimeError("source dispatcher did not stop before lock release")
        for run_id in self._futures:
            self._runtime.cancel(run_id)
        _, pending = wait(tuple(self._futures.values()), timeout=max(0.0, limit - monotonic()))
        if pending:
            raise RuntimeError("source runtimes remain active; polling lock is retained")
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)
        self._thread = None
