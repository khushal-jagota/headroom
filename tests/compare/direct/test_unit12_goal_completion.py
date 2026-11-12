"""Compare persisted achievements after corrections, direction edits, and clock passes.

The three pure completion tests cover checklist input precedence. They do not
prove historical retained evidence, canonical rows, source revisions, or the
participant memo. Reuse Python's canonical manual-measure fixture and a SQLite
backup. One Node companion adds no HTTP host, loop, provider, or stored oracle.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment
from tests.unit.test_manual_measures import manual_measure, replacement
from tests.unit.test_measure_persistence import NOW

from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.goals import actions
from planner.goals.contracts import GoalCompletionRule
from planner.goals.outcomes import reconcile_completions
from planner.goals.scheduling import GoalCompletionParticipant
from planner.measures.contracts import ManualWriteResult
from planner.measures.manual import write_value

ROOT = Path(__file__).resolve().parents[3]
TABLES = {
    "goals": "id",
    "measures": "id",
    "measure_observations": "measure_id,date",
    "manual_measure_writes": "request_id",
    "resource_source_versions": "source_kind,source_id",
}


def test_unit12_goal_completion_writer_parity(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), closing(connect(str(tmp_path / "seed.sqlite"))) as conn:
        create_schema(conn)
        measure = manual_measure(conn)
        rule: GoalCompletionRule = {
            "actual": {"op": "reference", "measure_id": measure.id, "day_offset": 0},
            "target": {"op": "constant", "value": 10},
            "direction": "atleast",
            "first_date": "2026-10-25",
        }
        goal = actions.create_goal(
            conn,
            {
                "project_id": "project_vylo",
                "title": "Retained milestone",
                "kind": "quantitative",
                "completion_rule": rule,
                "primary_chart": {
                    "type": "chart",
                    "id": "progress",
                    "kind": "number",
                    "placement": "primary",
                    "measure_id": measure.id,
                },
                "page": {
                    "kind": "headroom.page",
                    "version": 1,
                    "title": "Evidence",
                    "summary": "",
                    "blocks": [],
                },
            },
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
        target = tmp_path / "typescript.sqlite"
        with sqlite3.connect(target) as other:
            conn.backup(other)
        participant = GoalCompletionParticipant()
        result: list[dict[str, Any]] = []

        def snapshot(phase: str, outcome: object = None) -> None:
            result.append(
                {
                    "phase": phase,
                    "outcome": outcome,
                    "tables": {
                        table: [
                            dict(row)
                            for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
                        ]
                        for table, order in TABLES.items()
                    },
                }
            )

        def poll() -> None:
            participant.poll(conn, local_now=datetime.fromtimestamp(NOW, UTC), now=NOW)

        def replace(identifier: str, day: str, value: float, revision: int) -> ManualWriteResult:
            return write_value(
                conn,
                measure.id,
                replacement(identifier, day, value, revision),
                principal=OWNER_PRINCIPAL,
                now=NOW,
            )

        participant.start()
        poll()
        snapshot("initial")
        replace("completion-first", "2026-10-25", 12, 0)
        poll()
        snapshot("achieved")
        poll()
        snapshot("unchanged-poll")
        replace("completion-correction", "2026-10-25", 3, 1)
        poll()
        snapshot("correction-reopens")
        replay = replace("completion-correction", "2026-10-25", 3, 1)
        poll()
        snapshot("replayed-write", replay.replayed)
        replace("completion-later", "2026-10-26", 15, 2)
        snapshot("direct-reconcile", reconcile_completions(conn, now=NOW))
        snapshot("unchanged-reconcile", reconcile_completions(conn, now=NOW))
        actions.update_goal(
            conn,
            goal.id,
            {
                "completion_rule": {
                    **rule,
                    "direction": "atmost",
                    "target": {"op": "constant", "value": 0},
                }
            },
            principal=OWNER_PRINCIPAL,
            now=NOW,
        )
        snapshot("changed-direction")
        participant.stop()
        replace("completion-negative", "2026-10-27", -2.75, 3)
        poll()
        snapshot("stopped-participant")
        participant.start()
        poll()
        snapshot("restarted-participant")
        expected = result
    spec = tmp_path / "input.json"
    spec.write_text(
        json.dumps(
            {
                "db_path": str(target),
                "now": NOW,
                "measure_id": measure.id,
                "goal_id": goal.id,
                "rule": rule,
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
                "process.stdout.write(JSON.stringify("
                "run(JSON.parse(readFileSync(process.argv[1],'utf8')))));",
                str(spec),
                str(ROOT / "server/goals/testing/completion.ts"),
            ],
            cwd=ROOT,
            text=True,
        )
    )
    report = {
        "kind": "direct",
        "case": "unit12-goal-completion",
        "equal": actual == expected,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-goal-completion.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
