"""Compare captured source evidence through the real measure read action.

Pure rule tests cannot prove SQLite decoding of a run's frozen output or the
join from an observation to its captured definition. Reuse canonical Python
fixtures and one Node reader over a SQLite backup. No new HTTP host is needed.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_measure_persistence import NOW, collect, run, source, taken

from planner.core.contracts import CHIEF_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.measure_sources.records import encode_outputs
from planner.measures import actions, data
from planner.measures.contracts import (
    BinaryExpression,
    CandidateObservation,
    ConstantExpression,
    CountPeriod,
    CreateMeasureBody,
    HistoricalUnavailableReason,
    MeasureKind,
    MeasureOrigin,
    ReadMeasuresBody,
    ReferenceExpression,
    UpdateMeasureBody,
)

ROOT = Path(__file__).resolve().parents[3]


def test_unit12_taken_measure_read_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), closing(connect(str(tmp_path / "seed.sqlite"))) as conn:
        create_schema(conn)
        origin = source(conn)
        measure = taken(conn, origin, kind=MeasureKind.period_count, period=CountPeriod.day)
        run(conn, origin)
        conn.execute(
            "UPDATE measure_source_runs SET required_outputs=? WHERE id='run'",
            (encode_outputs(data.prepare_outputs(conn, origin, frozen_at=NOW)),),
        )
        conn.commit()
        candidates = []
        for day, value, clipped in (
            ("2025-12-31", -2.5, False),
            ("2026-01-01", 9007199254740994.0, False),
            ("2026-01-31", 3.125, True),
        ):
            local = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("Europe/Berlin"))
            start, end = local.timestamp(), (local + timedelta(days=1)).timestamp()
            candidates.append(
                CandidateObservation(
                    measure.id,
                    day,
                    value,
                    query_start_at=start + int(clipped),
                    query_end_at=end,
                    limitation="First second omitted" if clipped else None,
                )
            )
        candidates.append(
            CandidateObservation(
                measure.id, "2026-02-01", None, HistoricalUnavailableReason.provider_has_no_value
            )
        )
        collect(conn, origin, tuple(candidates))
        # A current revision differs from the frozen receipt without invalidating coverage.
        actions.update_measure(
            conn,
            measure.id,
            UpdateMeasureBody(expected_revision=1, name="Retained source evidence"),
            principal=CHIEF_PRINCIPAL,
            now=NOW,
        )
        derived = actions.create_measure(
            conn,
            CreateMeasureBody(
                project_id="project_vylo",
                name="Derived retained evidence",
                unit="items",
                origin=MeasureOrigin.assembled,
                kind=MeasureKind.reading,
                timezone="UTC",
                period=None,
                source_id=None,
                formula=BinaryExpression(
                    op="add",
                    left=ReferenceExpression(op="reference", measure_id=measure.id, day_offset=0),
                    right=ConstantExpression(op="constant", value=0.125),
                ),
            ),
            principal=CHIEF_PRINCIPAL,
            now=NOW,
        )
        reads = [
            ReadMeasuresBody(
                measure_ids=[measure.id, derived.id],
                first_date="2025-12-31",
                last_date="2026-01-01",
            ),
            ReadMeasuresBody(
                measure_ids=[measure.id, derived.id],
                first_date="2026-01-31",
                last_date="2026-02-02",
            ),
            ReadMeasuresBody(
                measure_ids=[measure.id, derived.id],
                first_date="2025-12-31",
                last_date="2026-01-01",
                limit=1,
            ),
        ]
        expected = [asdict(actions.read_values(conn, body, now=NOW)) for body in reads]
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
                str(ROOT / "server/measures/testing/taken.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    expected = json.loads(json.dumps(expected))
    report = {
        "kind": "direct",
        "case": "unit12-taken-measures",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-taken-measures.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
