"""Execute equivalent stored Python/JavaScript scripts through real child runtimes.

The writer comparison supplies RunResult directly. This case proves actual script
execution reaches the same retained rows, run receipt, health, and numeric bytes.
Reuse source/dataset fixtures and SQLite copies. No HTTP host or provider is added.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.support.measure_sources import NOW, configured_source
from tests.support.synthetic_homes import (
    create_synthetic_homes,
    synthetic_home_environment,
)
from tests.unit.test_dataset_persistence import dataset

from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.measure_sources import actions, data, run_actions, views
from planner.measure_sources.admission import prepare_admission
from planner.measure_sources.contracts import RunMode, SourceScriptBody
from planner.measure_sources.runtime import Runtime

ROOT = Path(__file__).resolve().parents[3]
RUN_ID = "source_run_unit13_actual_runtime"
TABLES = {
    "measure_sources": "id",
    "measures": "id",
    "datasets": "id",
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
PYTHON_SCRIPT = """output = context["required_outputs"][0]
record(output["measure_id"], output["required_date"], 12.5)
output = context["dataset_outputs"][0]
record_dataset(output["dataset_id"], output["required_date"],
    [{"dimensions": {"region": "de", "device": "web"}, "values": {"events": 1.0}}],
    query_end_at=context["frozen_at"], labels={"region": {"de": "Deutschland"}})
"""
JAVASCRIPT_SCRIPT = """export default async function ({context, record, record_dataset}) {
  const scalar = context.required_outputs[0];
  await record(scalar.measure_id, scalar.required_date, 12.5);
  const dataset = context.dataset_outputs[0];
  await record_dataset(dataset.dataset_id, dataset.required_date,
    [{dimensions: {region: 'de', device: 'web'}, values: {events: 1.0}}],
    {query_end_at: context.frozen_at, labels: {region: {de: 'Deutschland'}}});
}
"""
NORMALIZATIONS = {
    "active_script.text / measure_sources.active_script": "Programs use different languages.",
    "script SHA fields": "SHA identifies the language-specific source.",
    "metrics.elapsed_milliseconds / script_cpu_milliseconds": "Actual CPU and elapsed time vary.",
}


def normalized(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize only declared language text/digests and nondeterministic metrics."""
    result: dict[str, Any] = json.loads(json.dumps(snapshot))

    def metrics(value: dict[str, Any]) -> None:
        for key in ("elapsed_milliseconds", "script_cpu_milliseconds"):
            value[key] = "runtime-measurement"

    metrics(result["completed"]["run"]["metrics"])
    result["completed"]["run"]["script_sha256"] = "language-script-sha"
    result["source"]["active_script"]["text"] = "language-script"
    result["source"]["active_script"]["sha256"] = "language-script-sha"
    for row in result["tables"]["measure_sources"]:
        row["active_script"] = "language-script"
        row["active_script_sha256"] = "language-script-sha"
    for row in result["tables"]["measure_source_runs"]:
        row["script_sha256"] = "language-script-sha"
        retained_metrics = json.loads(row["metrics"])
        metrics(retained_metrics)
        row["metrics"] = retained_metrics
    return result


def test_unit13_source_runtime(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    with synthetic_home_environment(create_synthetic_homes(tmp_path)):
        base = tmp_path / "base.sqlite"
        with closing(connect(str(base))) as conn:
            create_schema(conn)
            source, _ = configured_source(conn)
            dataset(conn, source.id)
            python_path = tmp_path / "python.sqlite"
            typescript_path = tmp_path / "typescript.sqlite"
            for target in (python_path, typescript_path):
                with sqlite3.connect(target) as copy:
                    conn.backup(copy)
        for path, script in (
            (python_path, PYTHON_SCRIPT),
            (typescript_path, JAVASCRIPT_SCRIPT),
        ):
            with closing(connect(str(path))) as conn:
                current = data.read_source(conn, source.id)
                selected = actions.submit_script(
                    conn,
                    source.id,
                    SourceScriptBody(
                        expected_revision=current.source_revision, text=script
                    ),
                    principal=OWNER_PRINCIPAL,
                    now=NOW,
                )
                assert selected.active_script is not None
                # Fix only the generated identity. The runtime and writers remain real.
                with patch(
                    "planner.measure_sources.run_actions.new_id", return_value=RUN_ID
                ):
                    run_actions.request_run(
                        conn,
                        source.id,
                        mode=RunMode.collection,
                        history=None,
                        expected_script_revision=selected.active_script.revision,
                        principal=OWNER_PRINCIPAL,
                        now=NOW,
                    )
        key_path = tmp_path / "unused-synthetic-key"
        with closing(connect(str(python_path))) as conn:
            claimed = run_actions.claim_run(conn, RUN_ID, now=NOW)
            assert claimed is not None
            admission, credentials = prepare_admission(conn, claimed, key_path=key_path)
            result = Runtime().execute(admission, credentials)
            completed = run_actions.complete_run(conn, RUN_ID, result, now=NOW)
            expected = {
                "completed": asdict(completed),
                "source": asdict(data.read_source(conn, source.id)),
                "status": asdict(views.status(conn, source.id, now=NOW)),
                "tables": {
                    table: [
                        dict(row)
                        for row in conn.execute(
                            f"SELECT * FROM {table} ORDER BY {order}"
                        )
                    ]
                    for table, order in TABLES.items()
                },
            }
        assert completed.run.status.value == "succeeded"
        assert completed.run.observation_count == 1
        assert completed.run.dataset_result_count == 1
        assert completed.run.dataset_row_count == 1
        assert completed.run.metrics is not None
        assert (
            completed.run.metrics.request_count
            == completed.run.metrics.response_bytes
            == 0
        )
        spec = tmp_path / "runtime.json"
        spec.write_text(
            json.dumps(
                {
                    "db_path": str(typescript_path),
                    "key_path": str(key_path),
                    "source_id": source.id,
                    "run_id": RUN_ID,
                    "now": NOW,
                    "tables": TABLES,
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
                    "process.stdout.write(JSON.stringify(await "
                    "run(JSON.parse(readFileSync(process.argv[1],'utf8')))));",
                    str(spec),
                    str(ROOT / "server/measure_sources/testing/runtime.ts"),
                ],
                cwd=ROOT,
                text=True,
            )
        )
    expected_normalized, actual_normalized = normalized(expected), normalized(actual)
    report = {
        "kind": "direct",
        "case": "unit13-source-runtime",
        "equal": actual_normalized == expected_normalized,
        "normalizations": NORMALIZATIONS,
        "python": expected_normalized,
        "typescript": actual_normalized,
        "raw_python": expected,
        "raw_typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        folder = Path(destination)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "unit13-source-runtime.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual_normalized == expected_normalized
