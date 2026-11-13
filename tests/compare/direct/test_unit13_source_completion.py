"""Prove source completion writes that the route list cannot execute yet.

Reuse canonical source/dataset fixtures and two copies of synthetic SQLite state.
One Node companion proves atomic mixed batches, corrections, gaps, test receipts,
failure deduplication, closure, responsibility transfer and restart interruption.
No HTTP host or provider connection is added.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.support.measure_sources import NOW, configured_source
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)
from tests.unit.test_dataset_persistence import dataset
from tests.unit.test_manager_wakes import _item_and_ticket

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.datasets.contracts import CandidateDatasetResult, DatasetRow
from planner.measure_sources import actions, data, run_actions
from planner.measure_sources.contracts import (
    FailureCode,
    RunFailure,
    RunMetrics,
    RunMode,
    RunResult,
    SourceScriptBody,
)
from planner.measure_sources.notices import notice_is_current
from planner.measures.contracts import CandidateObservation, HistoricalUnavailableReason

ROOT = Path(__file__).resolve().parents[3]
TABLES = {
    "measure_sources": "id",
    "measure_source_runs": "id",
    "measure_observations": "measure_id,date",
    "measure_history_gaps": "measure_id,date",
    "dataset_results": "dataset_id,date",
    "dataset_rows": "dataset_id,date,dimension_key",
    "dataset_category_labels": "dataset_id,dimension,code",
    "measure_source_failure_episodes": "source_id,episode",
    "manager_wakes": "id",
    "resource_source_versions": "source_kind,source_id",
}


def test_unit13_source_completion(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    with (
        synthetic_home_environment(create_synthetic_homes(tmp_path)),
        closing(connect(str(tmp_path / "seed.sqlite"))) as conn,
    ):
        create_schema(conn)
        manager_id, _ = _item_and_ticket(conn, FakeClock(datetime.fromtimestamp(NOW, UTC)))
        source, measure = configured_source(conn)
        item = dataset(conn, source.id)
        source = data.read_source(conn, source.id)
        assert source.active_script is not None
        metrics = RunMetrics(0, 0, 0, 1, 0)

        def result(value: float | None, *, gap: bool = False, invalid: bool = False) -> RunResult:
            scalar = CandidateObservation(
                measure.id,
                "2026-10-26",
                value,
                HistoricalUnavailableReason.provider_has_no_value if gap else None,
                query_start_at=float(NOW - 86400),
                query_end_at=float(NOW),
            )
            rows = (
                (DatasetRow({"region": "de", "device": "web"}, {"events": value}),)
                if value is not None
                else ()
            )
            candidate = CandidateDatasetResult(
                item.id,
                "2026-10-26",
                rows,
                float(NOW),
                query_start_at=float(NOW - 86400),
                labels={"region": {"de": "Deutschland"}},
            )
            if invalid:
                candidate = CandidateDatasetResult("foreign", "2026-10-26", (), float(NOW))
            return RunResult((scalar,), None, metrics, (candidate,))

        phases: list[dict[str, Any]] = []
        values = [
            ("collection", result(9007199254740994.0), RunMode.collection),
            ("test-only", result(-2.5), RunMode.test),
            ("mixed-invalid-atomic", result(99.0, invalid=True), RunMode.collection),
            (
                "same-episode-failure",
                RunResult((), RunFailure(FailureCode.runtime_failure), metrics),
                RunMode.collection,
            ),
            ("correct-and-close", result(3.125), RunMode.collection),
            ("gap-and-empty-dataset", result(None, gap=True), RunMode.collection),
            (
                "new-failure",
                RunResult((), RunFailure(FailureCode.runtime_failure), metrics),
                RunMode.collection,
            ),
        ]
        for name, value, mode in values:
            run = run_actions.request_run(
                conn,
                source.id,
                mode=mode,
                history=None,
                expected_script_revision=source.active_script.revision,
                principal=OWNER_PRINCIPAL,
                now=NOW,
            )
            phases.append(
                {
                    "name": name,
                    "run_id": run.id,
                    "result_json": json.dumps(asdict(value)),
                }
            )
        phases.append({"name": "transfer-retires-old-notice", "operation": "transfer"})
        abandoned = run_actions.request_run(
            conn,
            source.id,
            mode=RunMode.collection,
            history=None,
            expected_script_revision=source.active_script.revision,
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
        stale = run_actions.request_run(
            conn,
            source.id,
            mode=RunMode.collection,
            history=None,
            expected_script_revision=source.active_script.revision,
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
        phases.extend(
            [
                {"name": "leave-running", "run_id": abandoned.id},
                {"name": "restart-interrupts", "operation": "abandon"},
                {"name": "script-change", "operation": "script"},
                {"name": "stale-claim-fails", "run_id": stale.id},
            ]
        )
        target = tmp_path / "typescript.sqlite"
        with sqlite3.connect(target) as other:
            conn.backup(other)
        expected = []
        for phase in phases:
            outcome: object = None
            if phase.get("operation") == "transfer":
                actions.set_responsible(
                    conn,
                    source.id,
                    Principal(PrincipalKind.manager, manager_id),
                    expected_responsibility_revision=data.read_source(
                        conn, source.id
                    ).responsibility_revision,
                    principal=OWNER_PRINCIPAL,
                    now=NOW,
                )
            elif phase.get("operation") == "script":
                actions.submit_script(
                    conn,
                    source.id,
                    SourceScriptBody(
                        expected_revision=data.read_source(conn, source.id).source_revision,
                        text="changed script",
                    ),
                    principal=OWNER_PRINCIPAL,
                    now=NOW,
                )
            elif phase.get("operation") == "abandon":
                outcome = run_actions.interrupt_abandoned_runs(conn, now=NOW)
            else:
                claimed = run_actions.claim_run(conn, phase["run_id"], now=NOW)
                if claimed and phase.get("result_json"):
                    value = next(value for name, value, _ in values if name == phase["name"])
                    outcome = asdict(
                        run_actions.complete_run(conn, phase["run_id"], value, now=NOW)
                    )
            expected.append(
                {
                    "phase": phase["name"],
                    "outcome": outcome,
                    "tables": {
                        table: [
                            dict(row)
                            for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
                        ]
                        for table, order in TABLES.items()
                    },
                    "current_notices": [
                        notice_is_current(conn, row)
                        for row in conn.execute("SELECT * FROM manager_wakes ORDER BY id")
                    ],
                }
            )
    expected = json.loads(json.dumps(expected))
    spec = tmp_path / "spec.json"
    spec.write_text(
        json.dumps(
            {
                "db_path": str(target),
                "now": NOW,
                "source_id": source.id,
                "manager_id": manager_id,
                "phases": phases,
            }
        )
    )
    actual = json.loads(
        subprocess.check_output(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {readFileSync} from 'node:fs'; "
                "import {pathToFileURL} from 'node:url'; "
                "const {run}=await import(pathToFileURL(process.argv[2]).href); "
                "const spec=JSON.parse(readFileSync(process.argv[1],'utf8')); "
                "process.stdout.write(JSON.stringify(run(spec)));",
                str(spec),
                str(ROOT / "server/measure_sources/testing/completion.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    report = {
        "kind": "direct",
        "case": "unit13-source-completion",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "accepted_differences": {"manager_wakes.summary": "Source names use native JSON quotes."},
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit13-source-completion.json").write_text(json.dumps(report, indent=2) + "\n")
    native_expected = json.loads(json.dumps(expected))
    for phase in native_expected:
        for wake in phase["tables"]["manager_wakes"]:
            wake["summary"] = wake["summary"].replace(
                f"Measure source {source.name!r} in ",
                f"Measure source {json.dumps(source.name, ensure_ascii=False)} in ",
                1,
            )
    assert actual == native_expected
