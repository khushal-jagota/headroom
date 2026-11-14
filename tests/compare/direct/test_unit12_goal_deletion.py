"""Compare Goal deletion across source responsibility, failure notices, and private files.

Pure completion and rule tests do not prove the responsibility transition or
file quarantine rollback. Reuse canonical Goal/source/run/file fixtures and
SQLite backups. A successful case and the existing abort-trigger boundary use
one Node companion, no HTTP host, provider execution, or source collection loop.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.support.measure_sources import configured_source
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_goal_pages import create
from tests.unit.test_measure_persistence import NOW
from tests.unit.test_measure_source_lifecycle import METRICS, _run

from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect, create_schema
from planner.files.goal_files import write_file
from planner.goals.actions import delete_goal
from planner.managers.data import create_manager
from planner.measure_sources import run_actions
from planner.measure_sources.contracts import FailureCode, RunFailure, RunResult
from planner.measures.contracts import CandidateObservation

ROOT = Path(__file__).resolve().parents[3]
TABLES = [
    "goals",
    "measure_sources",
    "measures",
    "measure_observations",
    "measure_source_runs",
    "measure_source_failure_episodes",
    "manager_wakes",
    "manager_wake_batches",
    "manager_wake_suppressions",
    "notification_deliveries",
    "notification_attention_edges",
    "notification_attention_state",
    "resource_source_versions",
]


def file_tree(root: Path) -> dict[str, Any]:
    return {
        "directories": sorted(
            str(path.relative_to(root)) for path in root.rglob("*") if path.is_dir()
        ),
        "files": {
            str(path.relative_to(root)): path.read_bytes().hex()
            for path in sorted(root.rglob("*"))
            if path.is_file()
        },
    }


def test_unit12_goal_deletion_transfer_and_files(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    seed = tmp_path / "seed" / "data.sqlite"
    seed.parent.mkdir()
    with synthetic_home_environment(homes), closing(connect(str(seed))) as conn:
        create_schema(conn)
        manager = create_manager(
            conn,
            title="Held milestone",
            project_id="project_vylo",
            clock=FakeClock(datetime.fromtimestamp(NOW, UTC)),
        )
        goal = create(conn, holder_kind="manager", holder_id=manager.id)
        survivor = create(conn, title="Keep private files")
        source, measure = configured_source(
            conn, responsible=Principal(PrincipalKind.goal, goal.id)
        )
        succeeded = _run(conn, source.id)
        run_actions.complete_run(
            conn,
            succeeded.id,
            RunResult((CandidateObservation(measure.id, "2026-10-27", 12.5),), None, METRICS),
            now=NOW,
        )
        failed = _run(conn, source.id, now=NOW + 1)
        run_actions.complete_run(
            conn,
            failed.id,
            RunResult(
                (),
                RunFailure(FailureCode.invalid_output, exception_type="ValueError", source_line=7),
                METRICS,
            ),
            now=NOW + 2,
        )
        for owner, relative_path, text in [
            (goal.id, "artifacts/nested/proof.md", "Delete me — exact bytes\n"),
            (goal.id, "report.txt", "Private goal report"),
            (survivor.id, "artifacts/keep.md", "Keep shared workspace intact"),
        ]:
            write_file(conn, OWNER_PRINCIPAL, owner, str(seed), relative_path, text)
        # Reuse the existing Goal deletion test's private notification/wake fixture rows.
        with conn:
            conn.execute(
                "INSERT INTO notification_push_subscriptions VALUES('sub','https://push.comparison.invalid','key','auth',1,1,NULL)"
            )
            conn.execute(
                "INSERT INTO notification_deliveries(subject_kind,subject_id,notification_type,"
                "generation,subscription_id,title,body,route,tag,created_at,status,"
                "next_attempt_at) "
                "VALUES('goal',?,'awaiting_reply',1,'sub','Goal','Reply','/goal','goal',1,'pending',1)",
                (goal.id,),
            )
            conn.execute(
                "INSERT INTO notification_attention_state VALUES('goal',?,'awaiting_reply',1,1)",
                (goal.id,),
            )
            conn.execute(
                "INSERT INTO notification_attention_edges VALUES('goal',?,'awaiting_reply',1,1,0)",
                (goal.id,),
            )
            conn.execute(
                "INSERT INTO manager_wakes(target_kind,target_id,source_kind,source_revision,"
                "summary,created_at) VALUES('goal',?,'turn_failure',1,'old',1)",
                (goal.id,),
            )
        cases = []
        expected = []
        for scenario in ["success", "rollback"]:
            paths = {}
            for side in ["python", "typescript"]:
                path = tmp_path / scenario / side / "data.sqlite"
                path.parent.mkdir(parents=True)
                with sqlite3.connect(path) as target:
                    conn.backup(target)
                    if scenario == "rollback":
                        target.execute(
                            "CREATE TRIGGER refuse_goal_delete BEFORE DELETE ON goals "
                            "BEGIN SELECT RAISE(ABORT,'delete failed'); END"
                        )
                shutil.copytree(seed.parent / "files", path.parent / "files")
                paths[side] = path
            with closing(connect(str(paths["python"]))) as subject:
                try:
                    outcome = asdict(
                        delete_goal(subject, goal.id, principal=OWNER_PRINCIPAL, now=NOW + 3)
                    )
                except sqlite3.IntegrityError as error:
                    outcome = {"error": str(error)}
                expected.append(
                    {
                        "scenario": scenario,
                        "outcome": outcome,
                        "tables": {
                            table: [
                                dict(row)
                                for row in subject.execute(f"SELECT * FROM {table} ORDER BY rowid")
                            ]
                            for table in TABLES
                        },
                        "files": file_tree(paths["python"].parent / "files"),
                        "foreign_keys": [
                            dict(row) for row in subject.execute("PRAGMA foreign_key_check")
                        ],
                    }
                )
            cases.append({"scenario": scenario, "db_path": str(paths["typescript"])})
    spec = tmp_path / "input.json"
    spec.write_text(
        json.dumps({"now": NOW + 3, "goal_id": goal.id, "cases": cases, "tables": TABLES})
    )
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
                str(ROOT / "server/goals/testing/deletion.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    native_expected = json.loads(json.dumps(expected))
    for case in native_expected:
        for wake in case["tables"]["manager_wakes"]:
            if wake["created_at"] == NOW + 3:
                wake["summary"] = wake["summary"].replace(
                    f"Measure source {source.name!r} in ",
                    f"Measure source {json.dumps(source.name, ensure_ascii=False)} in ",
                    1,
                )
    report = {
        "kind": "direct",
        "case": "unit12-goal-deletion",
        "equal": actual == native_expected,
        "python": expected,
        "typescript": actual,
        "accepted_differences": {"manager_wakes.summary": "Source names use native JSON quotes."},
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-goal-deletion.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == native_expected
