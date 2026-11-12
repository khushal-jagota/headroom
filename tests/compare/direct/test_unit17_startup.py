"""Fresh Python comparison of complete Codex child environments and launch requests.

All provider and service-manager launches stop at fences. Only the owned Node
export evaluator starts, with synthetic environment values and synthetic homes.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _python(spec: dict[str, Any]) -> dict[str, Any]:
    from planner.conversation.backends.codex_app_server import client
    from planner.conversation.worker_unit import WorkerUnit

    async def run() -> dict[str, Any]:
        cases = []
        for item in spec["cases"]:
            try:
                environment = client.child_environment(
                    overrides=item["overrides"], identity=item["identity"]
                )
                error = None
            except client.CodexLoginIsDuplicated as failure:
                environment = None
                error = str(failure)
            cases.append(
                {"name": item["name"], "environment": environment, "error": error}
            )
        launches = []
        for item in spec["launches"]:
            calls: list[dict[str, Any]] = []
            unit_stops = 0

            async def version(
                backend: str,
                executable: str,
                _calls: list[dict[str, Any]] = calls,
                **keywords: Any,
            ) -> None:
                assert backend == "codex"
                _calls.append(
                    {
                        "operation": "version",
                        "executable": executable,
                        "environment": keywords["environment"],
                        "working_directory": str(keywords["working_directory"]),
                    }
                )

            async def create(
                environment: dict[str, str],
                working_directory: Path,
                _calls: list[dict[str, Any]] = calls,
                _item: dict[str, Any] = item,
            ) -> WorkerUnit:
                _calls.append(
                    {
                        "operation": "unit-create",
                        "environment": environment,
                        "working_directory": str(working_directory),
                    }
                )
                directory = Path(_item["unit_directory"])
                temporary = str(directory / "t")
                return WorkerUnit(
                    name="panels-unit17-synthetic.service",
                    directory=directory,
                    environment={
                        **environment,
                        "TMPDIR": temporary,
                        "TMP": temporary,
                        "TEMP": temporary,
                    },
                    working_directory=working_directory,
                    slice_name="unit17-synthetic.slice",
                )

            async def stop(unit: WorkerUnit) -> None:
                nonlocal unit_stops
                unit_stops += 1

            async def spawn(
                *arguments: str,
                _calls: list[dict[str, Any]] = calls,
                **keywords: Any,
            ) -> None:
                _calls.append(
                    {
                        "operation": "spawn",
                        "argv": list(arguments),
                        "environment": keywords["env"],
                        "working_directory": keywords["cwd"],
                    }
                )
                raise OSError("synthetic stop before spawn")

            with (
                patch.object(client, "ensure_cli_compatible", version),
                patch.object(WorkerUnit, "create", create),
                patch.object(WorkerUnit, "stop", stop),
                patch.object(asyncio, "create_subprocess_exec", spawn),
            ):
                wire = client.CodexAppServerClient(
                    handler=cast(client.CodexServerMessageHandler, object()),
                    description="startup-parity",
                )
                try:
                    await wire.start(
                        argv=item["argv"],
                        environment=client.child_environment(),
                        working_directory=Path(item["working_directory"]),
                        worker_ticket_id=item["worker_ticket_id"],
                    )
                except client.CodexChildWouldNotStart as error:
                    assert str(error) == "synthetic stop before spawn"
            launches.append(
                {"name": item["name"], "calls": calls, "unit_stops": unit_stops}
            )
        return {
            "cases": cases,
            "launches": launches,
            "provider_processes_started": 0,
            "service_manager_calls": 0,
        }

    with patch.dict(os.environ, spec["ambient"], clear=True):
        return asyncio.run(run())


def test_unit17_startup_environment(
    tmp_path: Path, request: pytest.FixtureRequest
) -> None:
    """Protect account inheritance, copied-token refusal, and exact native argv."""
    started = time.perf_counter()
    home = tmp_path / "home"
    home.mkdir()
    ambient = home / "codex"
    alternate = home / "alternate"
    alias = home / "alias"
    for path in (ambient, alternate):
        path.mkdir()
        (path / "auth.json").write_text(
            json.dumps(
                {
                    "tokens": {
                        "account_id": path.name,
                        "refresh_token": "synthetic-unit17-credential",
                    }
                }
            )
        )
    alias.symlink_to(ambient)
    executable = tmp_path / "codex-placeholder"
    executable.write_text("Synthetic placeholder. Never execute.\n")
    executable.chmod(0o700)
    environment = {
        "PATH": os.environ.get("PATH", "/bin:/usr/bin"),
        "LANG": "C.UTF-8",
        "HOME": str(home),
        "PLAN_SERVER_URL": "http://127.0.0.1:0",
        "CODEX_HOME": str(ambient),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "HERMES_HOME": str(home / "hermes"),
        "TMPDIR": str(tmp_path),
        "CODEX_SYNTHETIC_ACCOUNT_OPTION": "preserve unchanged",
        "QUOTED": "quote\" slash\\ dollar$ backtick` percent% single' snowman☃",
    }
    spec = {
        "operation": "startup",
        "ambient": environment,
        "cases": [
            {"name": "inherited", "overrides": [], "identity": []},
            {
                "name": "precedence",
                "overrides": [["ORDER", "override"], ["EMPTY", ""]],
                "identity": [["ORDER", "identity"], ["PLAN_TICKET_ID", "synthetic"]],
            },
            {
                "name": "same-home-alias",
                "overrides": [["CODEX_HOME", str(alias)]],
                "identity": [],
            },
            {
                "name": "copied-secret-changed-account-metadata",
                "overrides": [["CODEX_HOME", str(alternate)]],
                "identity": [],
            },
        ],
        "launches": [
            {
                "name": name,
                "argv": [str(executable), "app-server", "--literal=$HOME"],
                "working_directory": str(tmp_path),
                "worker_ticket_id": identity,
                "unit_directory": str(tmp_path / "private % 'quoted'"),
            }
            for name, identity in (("owner", None), ("worker", "synthetic-worker"))
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
            "const {run_part1_parity}=await import(pathToFileURL(process.argv[2]).href); "
            "process.stdout.write(JSON.stringify(await run_part1_parity("
            "JSON.parse(readFileSync(process.argv[1],'utf8')))));",
            str(input_path),
            str(ROOT / "server/conversation/testing/codex/part1_parity.ts"),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    actual = json.loads(completed.stdout)
    python_script = ROOT / "src/planner/conversation/tool_shell_startup.bash"
    typescript_script = ROOT / "server/conversation/tool_shell_startup.bash"
    script_bytes_equal = python_script.read_bytes() == typescript_script.read_bytes()
    environment_differences = []
    for python_launch, typescript_launch in zip(
        expected["launches"], actual["launches"], strict=True
    ):
        for python_call, typescript_call in zip(
            python_launch["calls"], typescript_launch["calls"], strict=True
        ):
            python_environment = python_call["environment"]
            typescript_environment = typescript_call["environment"]
            differences = {
                name: {
                    "python": python_environment.get(name),
                    "typescript": typescript_environment.get(name),
                }
                for name in python_environment.keys() | typescript_environment.keys()
                if python_environment.get(name) != typescript_environment.get(name)
            }
            environment_differences.append(
                {
                    "launch": python_launch["name"],
                    "operation": python_call["operation"],
                    "variables": differences,
                }
            )
    report = {
        "case": "unit17-startup-environment",
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "real_children": ["owned Node export evaluator"],
        "normalization_rules": [],
        "environment_differences": environment_differences,
        "tool_shell_scripts": {
            "python": str(python_script),
            "typescript": str(typescript_script),
            "exact_bytes_equal": script_bytes_equal,
        },
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit17-startup-environment.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert script_bytes_equal
    assert actual["cases"] == expected["cases"]
    assert (
        actual["provider_processes_started"]
        == expected["provider_processes_started"]
        == 0
    )
    assert actual["service_manager_calls"] == expected["service_manager_calls"] == 0
    for difference in environment_differences:
        assert difference["variables"] == (
            {
                "BASH_ENV": {
                    "python": str(python_script),
                    "typescript": str(typescript_script),
                }
            }
            if sys.platform == "linux"
            else {}
        )
    for python_launch, typescript_launch in zip(
        expected["launches"], actual["launches"], strict=True
    ):
        assert typescript_launch["name"] == python_launch["name"]
        assert typescript_launch["unit_stops"] == python_launch["unit_stops"]
        for python_call, typescript_call in zip(
            python_launch["calls"], typescript_launch["calls"], strict=True
        ):
            assert {
                key: value
                for key, value in typescript_call.items()
                if key != "environment"
            } == {
                key: value for key, value in python_call.items() if key != "environment"
            }
