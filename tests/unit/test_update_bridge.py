"""The one-release bridge: the installed 53d64f0fd server installs this release.

Bridge only. The next release deletes this file with the `adopt` job and
scripts/bridge_legacy_adopt.py.
"""

from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from alembic.util.exc import CommandError
from scripts.bridge_legacy_adopt import PATCHES, patched

from planner.core.db import MIGRATIONS_DIRECTORY
from planner.environments.runtime_admission import native_tool_commands
from planner.environments.runtime_contracts import RuntimeContract
from planner.environments.update_contracts import SUPPORTED_UPDATE_SCHEMA_FLOOR

REPOSITORY = Path(__file__).resolve().parents[2]
WORKFLOW = yaml.safe_load((REPOSITORY / ".github/workflows/deploy.yml").read_text())
INSTALLED = "53d64f0fd"


def installed_source(path: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPOSITORY), "show", f"{INSTALLED}:{path}"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"installed release {INSTALLED} is not in this clone: {result.stderr}")
    return result.stdout


def function(source: str, name: str, namespace: dict[str, Any]) -> Any:
    """Define one top-level function from old source text, without importing its module."""
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    exec(compile(ast.Module([node], []), name, "exec"), namespace)
    return namespace[name]


def test_dispatch_inputs_are_exactly_declared() -> None:
    """Risk: GitHub refuses the old server's dispatch when an input is undeclared."""
    tree = ast.parse(installed_source("src/planner/environments/update_provider.py"))
    dispatch = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "dispatch"
    )
    payload = next(
        n.values[[cast(ast.Constant, k).value for k in n.keys].index("inputs")]
        for n in ast.walk(dispatch)
        if isinstance(n, ast.Dict) and any(
            isinstance(k, ast.Constant) and k.value == "inputs" for k in n.keys
        )
    )
    sent = {cast(ast.Constant, key).value for key in cast(ast.Dict, payload).keys}
    declared = WORKFLOW["on"]["workflow_dispatch"]["inputs"]
    assert set(declared) == sent
    assert "adopt" in declared["operation"]["options"]


def test_patched_admission_and_floor_admit_this_release(tmp_path: Path) -> None:
    """Risk: the old installer refuses this release's runtime or its migration graph."""
    admission_path, runner_path = PATCHES
    admission = installed_source(admission_path)
    old_probes = function(patched(admission_path, admission), "native_tool_commands",
                          {"Path": Path, "RuntimeContract": object})
    contract = cast(RuntimeContract, None)
    assert old_probes(tmp_path, None) == native_tool_commands(tmp_path, contract)

    database = tmp_path / "planner.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num TEXT)")
        conn.execute("INSERT INTO alembic_version VALUES ('goal_holders')")
    runner = installed_source(runner_path)
    namespace = {
        "Path": Path, "ScriptDirectory": ScriptDirectory, "ResolutionError": ResolutionError,
        "CommandError": CommandError, "sqlite3": sqlite3,
        "MIGRATIONS_DIRECTORY": MIGRATIONS_DIRECTORY,
    }
    original = function(runner, "validate_schema_ancestry", dict(namespace))
    with pytest.raises(ValueError, match="precedes the supported update schema floor"):
        original(database, MIGRATIONS_DIRECTORY)
    function(patched(runner_path, runner), "validate_schema_ancestry", namespace)(
        database, MIGRATIONS_DIRECTORY
    )


def test_receipt_keeps_the_schema_floor_the_old_server_requires() -> None:
    """Risk: the old server refuses to offer a receipt whose floor it does not know."""
    old = installed_source("src/planner/environments/update_contracts.py")
    assert f'SUPPORTED_UPDATE_SCHEMA_FLOOR = "{SUPPORTED_UPDATE_SCHEMA_FLOOR}"' in old


def test_adopt_runs_the_old_runner_with_its_exact_arguments(tmp_path: Path) -> None:
    """Risk: the bridge calls the old update runner with arguments it does not accept."""
    old_workflow = yaml.safe_load(installed_source(".github/workflows/deploy.yml"))
    systemd_run = tmp_path / "systemd-run"
    systemd_run.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$RECORDED"\n')
    systemd_run.chmod(0o755)
    environment = {
        "UPDATE_OPERATION": "adopt", "UPDATE_FAILED_DEPLOYMENT_ID": "", "UPDATE_PRIOR_SHA": "",
        "UPDATE_REQUEST_ID": "request",
        "UPDATE_PUBLICATION_RUN_ID": "1", "UPDATE_ARTIFACT_ID": "2",
        "UPDATE_PROOF_ARTIFACT_ID": "3", "UPDATE_APP_SHA": "b" * 40,
        "UPDATE_ARTIFACT_DIGEST": "c" * 64, "UPDATE_ARCHIVE_SHA256": "d" * 64,
        "GITHUB_RUN_ID": "4", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKSPACE": str(tmp_path),
        "PANELS_DEPLOY_VENV": str(tmp_path / "venv"), "PANELS_DEPLOYMENT_ID": "4-1",
        "PATH": str(tmp_path) + os.pathsep + os.defpath,
    }
    recorded: list[list[str]] = []
    for workflow in (old_workflow, WORKFLOW):
        step = next(s for s in workflow["jobs"]["adopt"]["steps"]
                    if s.get("name") == "Apply pinned owner request")
        assert set(step["env"]) <= set(environment)
        output = tmp_path / f"arguments-{len(recorded)}"
        subprocess.run(["/bin/bash", "-c", step["run"]],
                       env=environment | {"RECORDED": str(output)}, check=True)
        recorded.append(output.read_text().splitlines())
    assert recorded[1] == recorded[0]
    assert recorded[1][:4] == ["--user", "--wait", "--collect", "--pipe"]
