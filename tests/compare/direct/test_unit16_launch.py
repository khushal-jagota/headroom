"""Compare fresh complete Hermes launch values without a provider process.

Only an owned Node export evaluator starts. Hermes paths are synthetic strings.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _python(spec: dict[str, Any]) -> dict[str, Any]:
    from planner.conversation.backends.hermes_acp import (
        AcpChildLaunch,
        _child_environment,
        hermes_acp_child_launch,
    )
    from planner.conversation.contracts import (
        ConversationAccess,
        ConversationBackendKey,
        ConversationRoleMaterials,
        ResolvedConversationStart,
    )

    before = dict(os.environ)
    values = spec["launch"]
    launch = hermes_acp_child_launch(
        hermes_python=Path(values["hermes_python"]),
        hermes_home=Path(values["hermes_home"]),
        hermes_python_source_root=Path(values["hermes_python_source_root"]),
        panels_server_url=values["panels_server_url"],
    )
    cases = []
    for item in spec["cases"]:
        role = (
            None
            if item["identity"] is None
            else ConversationRoleMaterials(
                role_text="Synthetic launch identity",
                identity_environment_variables=tuple(map(tuple, item["identity"])),
            )
        )
        resolved = ResolvedConversationStart(
            conversation_id="unit16-launch",
            backend_key=ConversationBackendKey.hermes,
            model="synthetic-model",
            reasoning_effort=None,
            role_materials=role,
            workspace_folder=Path(spec["launch"]["hermes_home"]),
            access=ConversationAccess.full,
        )
        configured = AcpChildLaunch(
            argv=launch.argv,
            environment_overrides=launch.environment_overrides
            + tuple(map(tuple, item["overrides"])),
        )
        with patch.dict(os.environ, item["ambient"], clear=True):
            environment = _child_environment(configured, resolved)
        cases.append({"name": item["name"], "environment": environment})
    return {
        "launch": {
            "argv": list(launch.argv),
            "environment_overrides": [list(pair) for pair in launch.environment_overrides],
        },
        "cases": cases,
        "parent_environment_unchanged": dict(os.environ) == before,
    }


def test_unit16_launch_values(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Protect inheritance restriction, identity precedence, and retained script argv."""
    started = time.perf_counter()
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "CODEX_HOME": str(home / "codex"),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "TMPDIR": str(tmp_path),
    }
    ambient = {
        **environment,
        "LOGNAME": "synthetic-logname",
        "SHELL": "/synthetic/shell",
        "TERM": "synthetic-term",
        "USER": "synthetic-user",
        "UNRELATED_SECRET": "synthetic-must-not-inherit",
        "PLAN_ACTOR": "synthetic-ambient-must-not-inherit",
        "HERMES_YOLO_MODE": "synthetic-ambient-must-not-inherit",
        "BASH_ENV": "/synthetic/must-not-inherit",
    }
    spec = {
        "launch": {
            "hermes_python": str(home / "synthetic-install/python"),
            "hermes_home": str(home / "synthetic-hermes"),
            "hermes_python_source_root": str(home / "synthetic-source"),
            "panels_server_url": "https://panels.invalid/quoted?q=$HOME&x='value'",
        },
        "cases": [
            {"name": "owner", "ambient": ambient, "overrides": [], "identity": None},
            {
                "name": "worker-identity-wins",
                "ambient": ambient,
                "overrides": [
                    ["PLAN_ACTOR", "override"],
                    ["ORDER", "override"],
                    ["HERMES_YOLO_MODE", "override"],
                    ["EMPTY", ""],
                    ["QUOTED", "quote\" slash\\ dollar$ backtick` percent% single' snowman☃"],
                ],
                "identity": [
                    ["PLAN_ACTOR", "worker"],
                    ["PLAN_TICKET_ID", "unit16-synthetic-worker"],
                    ["PLAN_CONVERSATION_ID", "unit16-launch"],
                    ["ORDER", "identity"],
                ],
            },
            {
                "name": "absent-and-empty-inherited-values",
                "ambient": {"HOME": str(home), "TERM": ""},
                "overrides": [],
                "identity": [],
            },
        ],
    }
    expected = _python(spec)
    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps(spec))
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            "import {readFileSync} from 'node:fs'; "
            "import {pathToFileURL} from 'node:url'; "
            "const {run_launch_parity}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(run_launch_parity("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/hermes/part1_parity.ts"),
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
        "case": "unit16-launch-values",
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "provider_processes_started": 0,
        "real_children": ["owned Node export evaluator"],
        "normalization_rules": [],
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit16-launch-values.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert actual == expected
    assert actual["parent_environment_unchanged"] is True
    assert actual["launch"]["argv"][1] == str(
        ROOT / "src/planner/conversation/backends/hermes_acp_extension.py"
    )
    for case in actual["cases"]:
        assert case["environment"]["HERMES_YOLO_MODE"] == "1"
        assert "BASH_ENV" not in case["environment"]
        assert "UNRELATED_SECRET" not in case["environment"]
