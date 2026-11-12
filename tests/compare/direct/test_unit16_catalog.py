"""Fresh Python command outcomes against the Hermes catalog binding, with fake inventory."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROGRAM = r"""
const mode = process.argv[1];
if (mode === 'signal') process.kill(process.pid, 'SIGTERM');
else if (mode === 'timeout') {
  process.stdout.write('discarded partial'); setInterval(()=>{},1000);
}
else if (mode === 'failed') {
  process.stdout.write('partial inventory\n');
  process.stderr.write('catalog failed\n');
  process.exitCode=7;
} else if (mode === 'malformed') process.stdout.write('{not-json');
else process.stdout.write(JSON.stringify({
  schemaVersion:1,status:'runnable',defaultModelId:'fake:model',
  providers:[{id:'fake',displayName:'Fake',models:[{
    id:'fake:model',displayName:'Model',detail:'Synthetic model'
  }]}],
  inherited:process.env.UNIT16_INHERITED}));
"""


def _python(spec: dict[str, Any], environment: dict[str, str]) -> dict[str, Any]:
    from planner.conversation import snapshot
    from planner.environments import hermes_home

    async def exercise() -> dict[str, Any]:
        launches: list[dict[str, Any]] = []

        async def record(argv: tuple[str, ...], **keywords: Any) -> snapshot.CommandOutcome:
            launches.append({"argv": list(argv), **keywords})
            return snapshot.CommandOutcome(1, "", "")

        values = spec["launch"]
        with (
            patch.object(
                hermes_home, "resolve_hermes_python", return_value=Path(values["hermes_python"])
            ),
            patch.object(
                hermes_home, "resolve_planner_home", return_value=Path(values["hermes_home"])
            ),
            patch.object(
                hermes_home,
                "hermes_src_root",
                return_value=Path(values["hermes_python_source_root"]),
            ),
        ):
            for refresh in (False, True):
                await snapshot._hermes_catalog(
                    cast(
                        snapshot._CatalogRequest,
                        SimpleNamespace(
                            refresh=refresh,
                            environment=SimpleNamespace(run=record),
                        ),
                    )
                )
        backend = snapshot.SubprocessBackendProbeEnvironment()
        outcomes = []
        case_seconds: dict[str, float] = {}
        overrides = {
            "HERMES_HOME": values["hermes_home"],
            "HERMES_PYTHON_SRC_ROOT": values["hermes_python_source_root"],
            "PYTHONIOENCODING": "utf-8",
        }
        for item in spec["cases"]:
            argv = (
                [values["hermes_python"]]
                if item["name"] == "missing"
                else [*spec["command"], item["name"], *(["--refresh"] if item["refresh"] else [])]
            )
            case_started = time.perf_counter()
            result = await backend.run(
                argv, timeout_seconds=item["timeout_seconds"], environment_overrides=overrides
            )
            case_seconds[item["name"]] = time.perf_counter() - case_started
            outcomes.append(
                {
                    "name": item["name"],
                    "outcome": {
                        "exit_code": result.exit_code,
                        "standard_output": result.standard_output,
                        "standard_error": result.standard_error,
                    },
                }
            )
        return {"launches": launches, "outcomes": outcomes, "case_seconds": case_seconds}

    with patch.dict(os.environ, environment, clear=True):
        before = dict(os.environ)
        result = asyncio.run(exercise())
        unchanged = dict(os.environ) == before
    return {**result, "parent_environment_unchanged": unchanged}


def test_unit16_catalog_command_outcomes(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    started = time.perf_counter()
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "HERMES_HOME": str(home / "hermes"),
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "TMPDIR": str(tmp_path),
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "UNIT16_INHERITED": "synthetic full environment",
    }
    node = shutil.which("node")
    assert node is not None
    spec: dict[str, Any] = {
        "launch": {
            "hermes_python": str(home / "synthetic/python"),
            "hermes_home": str(home / "inventory-home"),
            "hermes_python_source_root": str(home / "synthetic-source"),
        },
        "command": [node, "-e", PROGRAM, "--"],
        "cases": [
            {
                "name": name,
                "timeout_seconds": 0.05 if name == "timeout" else 5,
                "refresh": name == "success",
            }
            for name in ("success", "failed", "timeout", "malformed", "missing", "signal")
        ],
    }
    expected = _python(spec, environment)
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url';"
            "const {run_catalog_parity}=await import(pathToFileURL(process.argv[2]).href);"
            "process.stdout.write(JSON.stringify(await run_catalog_parity("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/hermes/catalog_parity.ts"),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    actual = json.loads(completed.stdout)
    report = {
        "case": "unit16-catalog-command-outcomes",
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "normalization_rules": [],
        "real_children": [
            "Node export evaluator",
            "five synthetic inventory commands per language",
            "one refused nonexistent-interpreter spawn per language",
        ],
        "provider_processes_started": 0,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit16-catalog-command-outcomes.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    # Durations describe cost, while every command fact compares exactly.
    assert {key: value for key, value in actual.items() if key != "case_seconds"} == {
        key: value for key, value in expected.items() if key != "case_seconds"
    }
    outcomes = {item["name"]: item["outcome"] for item in actual["outcomes"]}
    from planner.conversation.snapshot import _parse_hermes_inventory

    inventory = _parse_hermes_inventory(json.loads(outcomes["success"]["standard_output"]))
    assert inventory is not None and len(inventory[0]) == 1 and inventory[1] == "fake:model"
    assert actual["parent_environment_unchanged"] is True
    assert outcomes["failed"]["exit_code"] == 7
    assert outcomes["timeout"]["exit_code"] == -1
    assert outcomes["timeout"]["standard_output"] == ""
    assert outcomes["malformed"]["standard_output"] == "{not-json"
