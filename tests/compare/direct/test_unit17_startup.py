"""Fresh Python startup parity with fenced processes and a synthetic scratch boundary.

Only the existing Node evaluator starts. P4 proves real scratch disk policy and removal.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _python(spec: dict[str, Any]) -> dict[str, Any]:
    from planner.conversation.backends.codex_app_server import client
    from planner.conversation.worker_scratch import WorkerScratch
    from planner.worker_supervision import WorkerSupervisor

    async def run() -> dict[str, Any]:
        cases = []
        for item in spec["cases"]:
            try:
                environment = client.child_environment(
                    overrides=item["overrides"], identity=item["identity"]
                )
                error = None
            except client.CodexLoginIsDuplicated as failure:
                environment, error = None, str(failure)
            cases.append({"name": item["name"], "environment": environment, "error": error})

        async def launch(item: dict[str, Any]) -> dict[str, Any]:
            calls: list[dict[str, Any]] = []

            async def version(backend: str, executable: str, **keywords: Any) -> None:
                assert backend == "codex"
                calls.append(
                    {
                        "operation": "version",
                        "executable": executable,
                        "environment": dict(keywords["environment"]),
                        "working_directory": str(keywords["working_directory"]),
                    }
                )
                if item["failure"] == "version":
                    raise OSError("synthetic version failure")

            async def cancel() -> None:
                calls.append({"operation": "cancel-unspawned"})

            async def stop(**_keywords: Any) -> SimpleNamespace:
                calls.append({"operation": "supervisor-stop"})
                return SimpleNamespace(complete=True)

            async def create(environment: dict[str, str], working_directory: Path) -> Any:
                calls.append(
                    {
                        "operation": "supervisor-create",
                        "environment": dict(environment),
                        "working_directory": str(working_directory),
                    }
                )
                supervisor = SimpleNamespace(
                    path=Path(item["directory"]) / "record.json",
                    working_directory=working_directory,
                    environment={**environment, "PLAN_WORKER_PROCESS_ROOT": item["directory"]},
                    cancel_unspawned=cancel,
                    stop=stop,
                )
                supervisor.argv = WorkerSupervisor.argv.__get__(supervisor)
                return supervisor

            async def close() -> None:
                calls.append({"operation": "scratch-close"})

            async def scratch(environment: dict[str, str]) -> Any:
                calls.append({"operation": "scratch-create", "environment": dict(environment)})
                if item["failure"] == "scratch":
                    raise RuntimeError("synthetic scratch failure")
                temporary = str(Path(item["directory"]) / "scratch")
                return SimpleNamespace(
                    environment={
                        **environment,
                        "TMPDIR": temporary,
                        "TMP": temporary,
                        "TEMP": temporary,
                    },
                    aclose=close,
                )

            async def reporter(path: str) -> None:
                calls.append({"operation": "supervision-report", "path": path})
                if item["failure"] == "reporter":
                    raise RuntimeError("synthetic reporter failure")

            async def spawn(*arguments: str, **keywords: Any) -> None:
                calls.append(
                    {
                        "operation": "spawn",
                        "argv": list(arguments),
                        "environment": dict(keywords["env"]),
                        "working_directory": keywords["cwd"],
                    }
                )
                if item["worker_ticket_id"]:
                    assert arguments == (
                        sys.executable,
                        "-I",
                        "-m",
                        "planner.worker_supervision.launch",
                        str(Path(item["directory"]) / "record.json"),
                        *item["argv"],
                    )
                raise OSError("synthetic stop before spawn")

            with (
                patch.object(client, "ensure_cli_compatible", version),
                patch.object(WorkerSupervisor, "create", create),
                patch.object(WorkerScratch, "create", scratch),
                patch.object(asyncio, "create_subprocess_exec", spawn),
            ):
                wire = client.CodexAppServerClient(
                    handler=cast(client.CodexServerMessageHandler, object()),
                    description="startup-parity",
                )
                failure = None
                try:
                    await wire.start(
                        argv=item["argv"],
                        environment=client.child_environment(),
                        working_directory=Path(item["working_directory"]),
                        worker_ticket_id=item["worker_ticket_id"],
                        supervision_reporter=reporter,
                    )
                except Exception as error:
                    failure = str(error)
            return {"name": item["name"], "calls": calls, "failure": failure}

        launches = [await launch(item) for item in spec["launches"]]
        return {
            "cases": cases,
            "launches": launches,
            "provider_processes_started": 0,
            "service_manager_calls": 0,
        }

    def forbidden(*_args: Any, **_keywords: Any) -> Any:
        raise AssertionError("Unexpected native launch")

    with (
        patch.dict(os.environ, spec["ambient"], clear=True),
        patch("subprocess.Popen", forbidden),
        patch.object(asyncio, "create_subprocess_shell", forbidden),
    ):
        return asyncio.run(run())


def test_unit17_startup_environment(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Protect complete environments, explicit Worker identity, and no-child cleanup order."""
    started = time.perf_counter()
    home = tmp_path / "home"
    home.mkdir()
    ambient, alternate, alias = home / "codex", home / "alternate", home / "alias"
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
        "PLAN_TICKET_ID": "inherited-worker",
        "CODEX_SYNTHETIC_ACCOUNT_OPTION": "preserve unchanged",
        "QUOTED": "quote\" slash\\ dollar$ backtick` percent% single' snowman☃",
    }
    spec = {
        "ambient": environment,
        "cases": [
            {"name": "inherited", "overrides": [], "identity": []},
            {
                "name": "precedence",
                "overrides": [["ORDER", "override"], ["EMPTY", ""]],
                "identity": [["ORDER", "identity"], ["PLAN_TICKET_ID", "synthetic"]],
            },
            {"name": "same-home-alias", "overrides": [["CODEX_HOME", str(alias)]], "identity": []},
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
                "directory": str(tmp_path / name),
                "failure": failure,
            }
            for name, identity, failure in (
                ("owner", None, "spawn"),
                ("worker", "synthetic-worker", "spawn"),
                ("preflight", "synthetic-worker", "version"),
                ("scratch-failure", "synthetic-worker", "scratch"),
                ("reporter-failure", "synthetic-worker", "reporter"),
            )
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
            "import {readFileSync} from 'node:fs'; import {pathToFileURL} from 'node:url'; "
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

    def comparable(value: dict[str, Any], prefix: int) -> dict[str, Any]:
        copied: dict[str, Any] = json.loads(json.dumps(value))
        for launch in copied["launches"]:
            for call in launch["calls"]:
                if call["operation"] == "spawn" and launch["name"] == "worker":
                    call["argv"] = ["<native-bootstrap>", *call["argv"][prefix:]]
        return copied

    report = {
        "case": "unit17-startup-environment",
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "real_children": ["owned Node evaluator"],
        "scratch_boundary": "Synthetic create/close; P4 proves disk policy and physical removal",
        "normalization_rules": [
            "Exact raw bootstrap asserted in each language; "
            "replace only Python -I/-m versus Node launch.ts prefix"
        ],
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        directory = Path(destination)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "unit17-startup-environment.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
    assert comparable(actual, 2) == comparable(expected, 4)
    expected_operations = {
        "owner": ["version", "spawn"],
        "worker": [
            "version",
            "supervisor-create",
            "scratch-create",
            "supervision-report",
            "spawn",
            "cancel-unspawned",
            "supervisor-stop",
            "scratch-close",
        ],
        "preflight": ["version"],
        "scratch-failure": [
            "version",
            "supervisor-create",
            "scratch-create",
            "cancel-unspawned",
            "supervisor-stop",
        ],
        "reporter-failure": [
            "version",
            "supervisor-create",
            "scratch-create",
            "supervision-report",
            "cancel-unspawned",
            "supervisor-stop",
            "scratch-close",
        ],
    }
    for launch in actual["launches"]:
        assert [call["operation"] for call in launch["calls"]] == expected_operations[
            launch["name"]
        ]
        assert launch["failure"] is not None
