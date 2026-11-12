"""Python parity inside the accepted policy, with fixed TS boundary assertions.

Pure calls have a different Python stack. Reuse the existing complete API app
and one TestClient for both real measure reads and real Goal readings. No new
server or provider starts. The TS companion proves the independent Goal seam,
not the as-yet-unported TS Goal HTTP routes. Python boundary answers are only
diagnostics: its interpreter and call path do not define the TypeScript policy.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from tests.support.api_app import _app
from tests.support.synthetic_homes import create_synthetic_homes, synthetic_home_environment

from planner.core.clock import TestClock as FakeClock
from planner.core.db import connect, create_schema

ROOT = Path(__file__).resolve().parents[3]
DAY = "2026-12-31"
NOW = int(datetime(2026, 12, 31, 12, tzinfo=UTC).timestamp())


class ArithmeticCase(TypedDict):
    label: str
    root: str
    independent: str
    weight: int
    gate_python_parity: bool


def wrapped(
    formula: dict[str, object],
    count: int,
    branch: bool = False,
) -> dict[str, object]:
    for _ in range(count):
        formula = (
            {
                "op": "if_before",
                "date": "2027-01-01",
                "before": formula,
                "otherwise": {"op": "constant", "value": 0.0},
            }
            if branch
            else {"op": "add", "left": formula, "right": {"op": "constant", "value": 0.0}}
        )
    return formula


def insert_measure(
    conn: sqlite3.Connection,
    identifier: str,
    formula: dict[str, object] | None,
) -> None:
    conn.execute(
        "INSERT INTO measures(id,project_id,name,unit,origin,kind,timezone,formula,"
        "definition_revision,created_at,updated_at) VALUES(?, 'project_vylo',?,'items',"
        "?,'reading','UTC',?,1,0,0)",
        (
            identifier,
            identifier,
            "assembled" if formula else "manual",
            json.dumps(formula) if formula else None,
        ),
    )
    if formula is None:
        conn.execute(
            "INSERT INTO manual_measure_writes VALUES(?,?,'owner','owner','synthetic',?,3,1,0)",
            (f"write-{identifier}", identifier, DAY),
        )
        conn.execute(
            "INSERT INTO measure_observations(measure_id,date,value,run_id,observed_at,"
            "definition_revision,manual_write_id) VALUES(?,?,3,NULL,0,1,?)",
            (identifier, DAY, f"write-{identifier}"),
        )


def python_projection(
    response: Response,
    *,
    goal: bool = False,
) -> list[dict[str, object]] | dict[str, object]:
    """Retain informational boundary failures without turning them into a test gate."""
    if response.status_code != 200:
        return {"http": response.status_code, "body": response.text}
    try:
        body = response.json()
        values = (
            [body["charts"][key]["points"][0]["actual"] for key in ("deep", "independent")]
            if goal
            else body["values"]
        )
        return [{"status": value["status"], "value": value["value"]} for value in values]
    except (ValueError, KeyError, TypeError, IndexError) as error:
        return {
            "http": response.status_code,
            "body": response.text,
            "projection_error": type(error).__name__,
        }


def test_unit12_arithmetic_recursion_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    cases: list[ArithmeticCase] = []
    homes = create_synthetic_homes(tmp_path)
    with synthetic_home_environment(homes), closing(connect(str(tmp_path / "seed.sqlite"))) as conn:
        create_schema(conn)
        insert_measure(conn, "independent", None)
        families = (
            ("reference", 0, 248, False),
            ("reference", 1, 198, False),
            ("reference", 15, 52, False),
            ("reference", 1, 198, True),
            ("sum_daily", 0, 247, False),
        )
        for index, (op, nesting, maximum, branch) in enumerate(families):
            for depth in range(maximum + 1):
                identifier = f"family-{index}-{depth}"
                formula = (
                    None
                    if depth == 0
                    else wrapped(
                        {
                            "op": op,
                            "measure_id": f"family-{index}-{depth - 1}",
                            **({"day_offset": 0} if op == "reference" else {"period": "day"}),
                        },
                        nesting,
                        branch,
                    )
                )
                insert_measure(conn, identifier, formula)
                interior_depths = (1, 10, 100, 200) if index == 0 else (10,)
                if depth in interior_depths:
                    cases.append(
                        {
                            "label": f"reference-depth-{depth}",
                            "root": identifier,
                            "independent": "independent",
                            "weight": 4 * depth,
                            "gate_python_parity": True,
                        }
                    )
                    cases[-1]["weight"] = (4 + nesting) * depth
            if index == 0:
                cases.append(
                    {
                        "label": "reference-depth-248",
                        "root": "family-0-248",
                        "independent": "independent",
                        "weight": 992,
                        "gate_python_parity": False,
                    }
                )
            if index != 0:
                base = maximum * (4 + nesting)
                for extra in range(max(0, 989 - base), 993 - base):
                    identifier = f"family-{index}-extra-{extra}"
                    formula = wrapped(
                        {
                            "op": op,
                            "measure_id": f"family-{index}-{maximum - 1}",
                            **({"day_offset": 0} if op == "reference" else {"period": "day"}),
                        },
                        nesting + extra,
                        branch,
                    )
                    insert_measure(conn, identifier, formula)
                    cases.append(
                        {
                            "label": f"mixed-{index}-{extra}",
                            "root": identifier,
                            "independent": "independent",
                            "weight": base + extra,
                            "gate_python_parity": False,
                        }
                    )
        conn.commit()
        app = _app(conn)
        app.state.clock = FakeClock(datetime(2026, 12, 31, 12, tzinfo=UTC))
        expected: list[dict[str, object]] = []
        with TestClient(app, raise_server_exceptions=False) as client:
            for case in cases:
                measures = client.post(
                    "/api/measures/read",
                    json={
                        "measure_ids": [case["root"], case["independent"]],
                        "first_date": DAY,
                        "last_date": DAY,
                    },
                )
                if case["gate_python_parity"]:
                    assert measures.status_code == 200, measures.text
                created = client.post(
                    "/api/goals",
                    json={
                        "project_id": "project_vylo",
                        "title": case["label"],
                        "kind": "binary",
                        "primary_checklist_block_id": None,
                        "page": {
                            "kind": "headroom.page",
                            "version": 1,
                            "title": "Boundary",
                            "summary": "",
                            "blocks": [
                                {
                                    "type": "chart",
                                    "id": label,
                                    "kind": "line",
                                    "title": label,
                                    "measure_id": measure_id,
                                    "expectation": {"op": "constant", "value": 9},
                                }
                                for label, measure_id in (
                                    ("deep", case["root"]),
                                    ("independent", case["independent"]),
                                )
                            ],
                        },
                    },
                )
                if case["gate_python_parity"]:
                    assert created.status_code == 200, created.text
                if created.status_code != 200:
                    expected.append(
                        {
                            "measures": {"http": measures.status_code, "body": measures.text},
                            "goal_actuals": {"http": created.status_code, "body": created.text},
                        }
                    )
                    continue
                goal = client.get(
                    f"/api/goals/{created.json()['id']}/readings",
                    params={
                        "first_date": DAY,
                        "last_date": DAY,
                    },
                )
                if case["gate_python_parity"]:
                    assert goal.status_code == 200, goal.text

                expected.append(
                    {
                        "measures": python_projection(measures),
                        "goal_actuals": python_projection(goal, goal=True),
                    }
                )
        target = tmp_path / "typescript.sqlite"
        with sqlite3.connect(target) as other:
            conn.backup(other)
    actual = json.loads(
        subprocess.check_output(
            ["node", str(ROOT / "server/measures/testing/recursion.ts")],
            input=json.dumps({"db_path": str(target), "now": NOW, "day": DAY, "roots": cases}),
            cwd=ROOT,
            text=True,
        )
    )
    parity_pass = all(
        actual[index] == expected[index]
        for index, case in enumerate(cases)
        if case["gate_python_parity"]
    )
    policy_expected = []
    for case in cases:
        value = {"status": "value", "value": 3.0}
        limited = {"status": "arithmetic_limit", "value": None}
        policy_expected.append(
            {
                "measures": [value, value] if case["weight"] <= 991 else [limited, limited],
                "goal_actuals": [value if case["weight"] <= 989 else limited, value],
            }
        )
    policy_pass = all(
        actual[index] == policy_expected[index]
        for index, case in enumerate(cases)
        if not case["gate_python_parity"]
    )
    report = {
        "python_version": sys.version,
        "python_recursion_limit": sys.getrecursionlimit(),
        "cases": cases,
        "python": expected,
        "typescript": actual,
        "python_parity_cases": sum(case["gate_python_parity"] for case in cases),
        "fixed_ts_policy_cases": sum(not case["gate_python_parity"] for case in cases),
        "python_parity_pass": parity_pass,
        "typescript_policy_pass": policy_pass,
        "typescript_policy_expected": policy_expected,
        "informational_python_all_equal": actual == expected,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-recursion.json").write_text(json.dumps(report, indent=2) + "\n")
    assert parity_pass
    assert policy_pass


def test_unit12_demand_recursion_parity(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """A separate one-frame traversal must not inherit the four-frame arithmetic cutoff.

    Python equality gates only the four interior cases. Its historical six-frame
    call path remains diagnostic at the fixed TS boundary. Public graph admission
    can hide this separate demand boundary from an ordinary HTTP request.
    """
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    with (
        synthetic_home_environment(homes),
        closing(connect(str(tmp_path / "demand.sqlite"))) as conn,
    ):
        create_schema(conn)
        insert_measure(conn, "independent", None)
        for depth in range(993):
            insert_measure(
                conn,
                f"demand-{depth}",
                None
                if depth == 0
                else {
                    "op": "reference",
                    "measure_id": f"demand-{depth - 1}",
                    "day_offset": 0,
                },
            )
        conn.commit()
        target = tmp_path / "typescript.sqlite"
        with sqlite3.connect(target) as other:
            conn.backup(other)
    spec = {
        "db_path": str(target),
        "now": NOW,
        "day": DAY,
        "mode": "demand",
        "demand_roots": [f"demand-{depth}" for depth in (1, 10, 100, 200, 990, 991, 992)],
    }
    oracle = """import json, sqlite3, sys
from planner.measures.data import read_definitions
from planner.measures.contracts import MeasureDate
from planner.measures.logic.evaluation import demanded_pairs
spec=json.load(sys.stdin)
conn=sqlite3.connect(spec['db_path']);conn.row_factory=sqlite3.Row

def outer(): return middle()
def middle(): return inner()
def inner(): return last()
def last():
    results=[]
    for root in spec['demand_roots']:
        identifiers=(root,'independent')
        definitions=read_definitions(conn,identifiers)
        try:
            pairs=demanded_pairs(tuple(MeasureDate(i,spec['day']) for i in identifiers),
                                 definitions,now=spec['now'])
            value={'root':root,'status':'value','count':len(pairs)}
        except RecursionError:
            value={'root':root,'status':'arithmetic_limit','count':None}
        results.append(value)
    return results
print(json.dumps(outer()))
"""
    expected = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", oracle],
            input=json.dumps(spec),
            cwd=ROOT,
            text=True,
        )
    )
    actual = json.loads(
        subprocess.check_output(
            ["node", str(ROOT / "server/measures/testing/recursion.ts")],
            input=json.dumps(spec),
            cwd=ROOT,
            text=True,
        )
    )
    policy_expected = [
        {"root": f"demand-{depth}", "status": "value", "count": depth + 2} for depth in (990, 991)
    ] + [{"root": "demand-992", "status": "arithmetic_limit", "count": None}]
    parity_pass = actual[:4] == expected[:4]
    policy_pass = actual[4:] == policy_expected
    report = {
        "case": "demand-expansion",
        "python_version": sys.version,
        "python_recursion_limit": sys.getrecursionlimit(),
        "python": expected,
        "typescript": actual,
        "python_parity_cases": 4,
        "fixed_ts_policy_cases": 3,
        "python_parity_pass": parity_pass,
        "typescript_policy_pass": policy_pass,
        "typescript_policy_expected": policy_expected,
        "informational_python_all_equal": actual == expected,
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-demand-recursion.json").write_text(json.dumps(report, indent=2) + "\n")
    assert parity_pass
    assert policy_pass


def test_unit12_mixed_formula_admission_parity(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    """Interior Python parity and fixed-policy parser and graph neighbors.

    Python's runtime stack does not gate accepted TS boundary outcomes. Interior
    equality still proves the actual TS action/write door and canonical rows on
    pre-write SQLite backups. This starts no external server or provider.
    """
    started = time.perf_counter()
    homes = create_synthetic_homes(tmp_path)
    results = []
    with synthetic_home_environment(homes), closing(connect(str(tmp_path / "seed.sqlite"))) as conn:
        create_schema(conn)
        for depth in range(993):
            insert_measure(
                conn,
                f"node-{depth}",
                None
                if depth == 0
                else {
                    "op": "reference",
                    "measure_id": f"node-{depth - 1}",
                    "day_offset": 0,
                },
            )
        conn.commit()
        app = _app(conn)
        app.state.clock = FakeClock(datetime(2026, 12, 31, 12, tzinfo=UTC))
        cases = (
            (True, "reference", 10, 100),
            (True, "reference_at", 9, 100),
            (True, "constant", 8, 100),
            (False, "reference", 10, 980),
            (False, "reference", 11, 980),
            (False, "reference_at", 9, 980),
            (False, "reference_at", 10, 980),
            (False, "constant", 8, 980),
            (False, "constant", 9, 980),
            (False, "reference", 0, 989),
            (False, "reference", 0, 990),
            (False, "reference", 0, 991),
        )
        with TestClient(app, raise_server_exceptions=False) as client:
            for index, (gate_parity, kind, nesting, graph_depth) in enumerate(cases):
                case_started = time.perf_counter()
                conn.execute(
                    "DELETE FROM measure_dependencies WHERE measure_id IN "
                    "(SELECT id FROM measures WHERE name='Admission proof')"
                )
                conn.execute("DELETE FROM measures WHERE name='Admission proof'")
                leaf: dict[str, object] = {"op": kind}
                if kind == "constant":
                    leaf["value"] = 3.0
                else:
                    leaf["measure_id"] = "node-0"
                    leaf["day_offset" if kind == "reference" else "date"] = (
                        0 if kind == "reference" else DAY
                    )
                nested = leaf
                for _ in range(nesting):
                    nested = {
                        "op": "if_before",
                        "date": "2027-01-01",
                        "before": nested,
                        "otherwise": {"op": "reference", "measure_id": "node-0", "day_offset": 0},
                    }
                conn.execute(
                    "UPDATE measures SET formula=? WHERE id='node-1'", (json.dumps(nested),)
                )
                conn.commit()
                target = tmp_path / f"typescript-{index}.sqlite"
                with sqlite3.connect(target) as other:
                    conn.backup(other)
                body = {
                    "project_id": "project_vylo",
                    "name": "Admission proof",
                    "unit": "items",
                    "origin": "assembled",
                    "kind": "reading",
                    "timezone": "UTC",
                    "period": None,
                    "source_id": None,
                    "formula": {
                        "op": "reference",
                        "measure_id": f"node-{graph_depth}",
                        "day_offset": 0,
                    },
                }
                response = client.post("/api/measures", json=body)
                expected = {
                    "status": response.status_code,
                    "body": {"status": "accepted"}
                    if response.status_code == 200
                    else (
                        response.json()
                        if response.headers.get("content-type", "").startswith("application/json")
                        else response.text
                    ),
                    "rows": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT project_id,name,unit,origin,kind,timezone,period,source_id,"
                            "formula,"
                            "definition_revision,write_revision,created_at,updated_at "
                            "FROM measures WHERE name='Admission proof'"
                        )
                    ],
                    "dependencies": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT input_measure_id FROM measure_dependencies d "
                            "JOIN measures m ON m.id=d.measure_id WHERE m.name='Admission proof' "
                            "ORDER BY input_measure_id"
                        )
                    ],
                }
                actual = json.loads(
                    subprocess.check_output(
                        ["node", str(ROOT / "server/measures/testing/recursion.ts")],
                        input=json.dumps(
                            {"mode": "admission", "db_path": str(target), "now": NOW, "body": body}
                        ),
                        cwd=ROOT,
                        text=True,
                    )
                )
                leaf_cost = {"reference": 0, "reference_at": 1, "constant": 2}[kind]
                accepted = graph_depth + nesting + leaf_cost <= 990 and graph_depth + 1 <= 991
                policy_row = {
                    **body,
                    "formula": json.dumps(body["formula"]),
                    "definition_revision": 1,
                    "write_revision": 0,
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                policy_expected = {
                    "status": 200 if accepted else 400,
                    "body": {"status": "accepted"}
                    if accepted
                    else {
                        "error": {
                            "code": "validation",
                            "message": "measure expression exceeds interpreter nesting limit",
                            "detail": {},
                        }
                    },
                    "rows": [policy_row] if accepted else [],
                    "dependencies": [{"input_measure_id": f"node-{graph_depth}"}]
                    if accepted
                    else [],
                }
                results.append(
                    {
                        "leaf": kind,
                        "graph_depth": graph_depth,
                        "total_graph_edges": graph_depth + 1,
                        "AST_nesting": nesting,
                        "fixed_budget": "graph" if nesting == 0 else "parser",
                        "gate_python_parity": gate_parity,
                        "python": expected,
                        "typescript": actual,
                        "typescript_policy_expected": policy_expected if not gate_parity else None,
                        "gate_pass": actual == (expected if gate_parity else policy_expected),
                        "informational_python_equal": actual == expected,
                        "seconds": time.perf_counter() - case_started,
                    }
                )
    report = {
        "case": "mixed-formula-admission",
        "python_version": sys.version,
        "python_recursion_limit": sys.getrecursionlimit(),
        "results": results,
        "python_parity_cases": 3,
        "fixed_ts_policy_cases": 9,
        "fixed_graph_neighbors_seconds": sum(
            result["seconds"] for result in results if result["fixed_budget"] == "graph"
        ),
        "seconds": time.perf_counter() - started,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit12-formula-admission.json").write_text(json.dumps(report, indent=2) + "\n")
    assert all(result["gate_pass"] for result in results)
