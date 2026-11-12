"""Prove retained dataset reads that the HTTP source host cannot populate.

Reuse Python's canonical source, dataset and retained-result writers. One Node
reader consumes a SQLite backup. This adds no server or provider connection.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_dataset_persistence import dataset
from tests.unit.test_measure_persistence import NOW, run, source

from planner.core.db import connect, create_schema
from planner.datasets import data
from planner.datasets.contracts import CandidateDatasetResult, DatasetRow, ReadDatasetsBody
from planner.datasets.logic.validation import dimension_key
from planner.measures.data import transaction

ROOT = Path(__file__).resolve().parents[3]


def test_unit12_retained_dataset_read_parity(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), closing(connect(str(tmp_path / "seed.sqlite"))) as conn:
        create_schema(conn)
        item = dataset(conn, source(conn))
        run(conn, item.source_id)
        candidates = (
            CandidateDatasetResult(
                item.id,
                "2025-12-31",
                (
                    DatasetRow({"region": "de", "device": "web"}, {"events": -2.5}),
                    DatasetRow({"region": "us", "device": "web"}, {"events": 9007199254740994.0}),
                ),
                NOW,
                labels={"region": {"de": "Deutschland", "us": "United States"}},
            ),
            CandidateDatasetResult(item.id, "2026-01-01", (), NOW),
            CandidateDatasetResult(
                item.id, "2026-01-02", (), NOW, unavailable_reason="No retained history"
            ),
            CandidateDatasetResult(
                item.id,
                "2026-01-31",
                (DatasetRow({"region": "fr", "device": "app"}, {"events": 3.125}),),
                NOW,
                limitation="A partial historical window",
            ),
        )
        with transaction(conn):
            data.commit_results(
                conn,
                run_id="run",
                source_id=item.source_id,
                outputs=data.prepare_outputs(conn, item.source_id, frozen_at=NOW),
                candidates=candidates,
                collected_at=NOW,
            )
        reads = [
            ReadDatasetsBody(
                dataset_ids=[item.id], first_date="2025-12-31", last_date="2026-02-01"
            ),
            ReadDatasetsBody(
                dataset_ids=[item.id], first_date="2025-12-31", last_date="2026-02-01", limit=1
            ),
        ]
        expected = {
            "unicode_tuple_key": dimension_key({"\U00010000": "\x7f", "\ue000": "é"}),
            "reads": [asdict(data.read_values(conn, body, now=NOW)) for body in reads],
            "earliest": data.earliest_retained_date(conn, item.id),
        }
        target = tmp_path / "typescript.sqlite"
        with sqlite3.connect(target) as other:
            conn.backup(other)
    spec = tmp_path / "input.json"
    spec.write_text(json.dumps({"db_path": str(target), "now": NOW, "requests": reads}))
    actual = json.loads(
        subprocess.check_output(
            [
                "node",
                "--input-type=module",
                "-e",
                "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
                "const {run}=await import(pathToFileURL(process.argv[2]).href); "
                "process.stdout.write(JSON.stringify(run(JSON.parse(readFileSync(process.argv[1],'utf8')))));",
                str(spec),
                str(ROOT / "server/datasets/testing/direct.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    expected = json.loads(json.dumps(expected))
    report = {
        "kind": "direct",
        "case": "unit12-retained-datasets",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-retained-datasets.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
