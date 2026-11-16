"""Fresh Python parity for containment bytes and selected-CLI startup errors.

No service manager or provider process starts. The only real child is the Node
export evaluator. Both language boundaries reject unexpected child launches.
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


def _python_bytes(spec: dict[str, Any]) -> dict[str, Any]:
    from planner import worker_supervision
    from planner.conversation.local_child_contracts import LocalHostBootIdentity
    from planner.worker_supervision import records
    from planner.worker_supervision.contracts import InspectionContext, WorkerProcess

    attempts: list[str] = []

    def forbidden(*arguments: Any, **_keywords: Any) -> Any:
        attempts.append(str(arguments[0]))
        raise AssertionError("The bootstrap comparison must not start a process")

    owner = WorkerProcess(**spec["owner"])
    primitives = SimpleNamespace(snapshot=lambda _deadline: {owner.pid: owner})
    context = InspectionContext(
        cast(Any, primitives), LocalHostBootIdentity("synthetic-host", "synthetic-boot")
    )

    async def run() -> dict[str, Any]:
        supervisor = await worker_supervision.WorkerSupervisor.create(
            spec["environment"], Path(spec["working_directory"])
        )
        assert supervisor is not None
        pending = records.read(supervisor.path)
        argv: list[list[str] | dict[str, str]] = []
        for command in spec["commands"]:
            try:
                result = list(supervisor.argv(command))
                assert result[:5] == [
                    sys.executable,
                    "-I",
                    "-m",
                    "planner.worker_supervision.launch",
                    str(supervisor.path),
                ]
                argv.append(result)
            except (ValueError, OSError) as error:
                argv.append({"error": str(error)})
        await supervisor.cancel_unspawned()
        return {
            "argv": argv,
            "environment": supervisor.environment,
            "pending": pending,
            "revoked": records.read(supervisor.path),
            "child_launch_attempts": attempts,
        }

    with (
        patch.object(asyncio, "create_subprocess_exec", forbidden),
        patch.object(asyncio, "create_subprocess_shell", forbidden),
        patch("subprocess.Popen", forbidden),
        patch.object(os, "getpid", return_value=owner.pid),
        patch.object(worker_supervision, "_inspection_context", return_value=context),
        patch.object(
            worker_supervision,
            "uuid4",
            return_value=SimpleNamespace(hex=spec["uuid"].replace("-", "")),
        ),
    ):
        return asyncio.run(run())


def _node_environment(root: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT"}
    }
    home = root / "home"
    home.mkdir()
    environment.update(
        HOME=str(home),
        CODEX_HOME=str(home / "codex"),
        CLAUDE_CONFIG_DIR=str(home / "claude"),
        HERMES_HOME=str(home / "hermes"),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        TMPDIR=str(root),
    )
    return environment


def _node(spec: dict[str, Any], root: Path) -> dict[str, Any] | list[dict[str, Any]]:
    input_path = root / "input.json"
    input_path.write_text(json.dumps(spec))
    result = subprocess.run(
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
            str(ROOT / "server/conversation/testing/claude/part1_parity.ts"),
        ],
        cwd=ROOT,
        env=_node_environment(root),
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return cast(dict[str, Any] | list[dict[str, Any]], json.loads(result.stdout))


def test_unit18_containment_bytes(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Protect real bootstrap arguments and durable no-child revocation without execution."""
    started = time.perf_counter()
    working = tmp_path / "work % 'quoted'"
    binary = working / "relative-bin"
    binary.mkdir(parents=True)
    executable = binary / "fake-cli"
    executable.write_text("Scripted placeholder. Never execute.\n")
    executable.chmod(0o700)
    (binary / "linked-cli").symlink_to(executable)
    private = tmp_path / "private % 'quoted'"
    environment = {
        "PATH": str(binary),
        "PLAN_TICKET_ID": "synthetic-byte-proof",
        "PLAN_CONVERSATION_ID": "synthetic-conversation",
        "PLAN_WORKER_PROCESS_ROOT": str(private),
        "PLAN_EXECUTOR_CHILD_ID": "synthetic-child",
        "PLAN_EXECUTOR_SOURCE_INCARNATION": "synthetic-incarnation",
        "QUOTED": "quote\" slash\\ dollar$ backtick` percent% single' snowman☃",
        "MULTILINE": "line one\nline two\r\nline three",
        "EMPTY": "",
    }
    spec = {
        "operation": "bytes",
        "environment": environment,
        "working_directory": str(working),
        "uuid": "33333333-3333-3333-3333-333333333333",
        "owner": {
            "pid": 41,
            "birth": "synthetic-birth",
            "parent": 1,
            "group": 41,
            "session": 41,
            "zombie": False,
        },
        "commands": [
            [str(executable), "--value=$HOME", "`not-a-command`", "one\ntwo", "100%", ""],
            ["relative-bin/fake-cli", "relative"],
            ["fake-cli", "path-lookup"],
            ["linked-cli", "preserve-symlink"],
            [],
            ["absent-cli"],
        ],
    }
    expected, actual = _python_bytes(spec), _node(spec, tmp_path)
    assert isinstance(actual, dict)

    def comparable(value: dict[str, Any], prefix: int) -> dict[str, Any]:
        return {
            **value,
            "argv": [
                ["<native-bootstrap>", *command[prefix:]] if isinstance(command, list) else command
                for command in value["argv"]
            ],
        }

    report = {
        "case": "unit18-containment-bytes",
        "inputs": spec,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "real_children": ["owned Node evaluator"],
        "service_manager_calls": 0,
        "normalization_rules": [
            "Exact raw bootstrap asserted in each language; "
            "replace only Python -I/-m versus Node launch.ts prefix"
        ],
        "boundary": (
            "Real create/cancel and private journal I/O with synthetic native process evidence; "
            "bootstrap is never executed"
        ),
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit18-containment-bytes.json").write_text(json.dumps(report, indent=2) + "\n")
    assert comparable(actual, 2) == comparable(expected, 4)
    assert actual["pending"]["state"] == "pending"
    assert actual["pending"]["root"] is None
    assert actual["revoked"] == {
        **actual["pending"],
        "state": "complete",
        "launch_revoked": True,
        "reason": "The OS refused spawn before a child existed",
    }
    assert actual["child_launch_attempts"] == []


def _python_versions(cases: list[dict[str, Any]], executable: str) -> list[dict[str, Any]]:
    from planner.conversation.backends import cli_compatibility

    class FakeProcess:
        def __init__(self, values: dict[str, Any]) -> None:
            self.values = values
            self.returncode: int | None = (
                None if values.get("failure") == "timeout" else values["exit_code"]
            )

        async def communicate(self) -> tuple[bytes, bytes]:
            if self.values.get("failure") == "timeout":
                raise TimeoutError("synthetic timeout")
            return self.values["stdout"].encode(), b"synthetic stderr"

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    async def run() -> list[dict[str, Any]]:
        results = []
        for case in cases:

            async def fake_spawn(
                *arguments: Any, _case: dict[str, Any] = case, **keywords: Any
            ) -> FakeProcess:
                assert arguments == (executable, "--version")
                if _case.get("failure") == "spawn":
                    raise OSError("synthetic launch refusal")
                return FakeProcess(_case)

            with patch.object(asyncio, "create_subprocess_exec", fake_spawn):
                try:
                    await cli_compatibility.ensure_cli_compatible("claude", executable)
                    result = None
                except cli_compatibility.BackendCliIncompatible as error:
                    result = str(error)
            results.append({"name": case["name"], "result": result})
        return results

    return asyncio.run(run())


def test_unit18_cli_version_errors(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Protect the chosen executable's feature floor and exact repair messages."""
    started = time.perf_counter()
    cases = [
        {"name": "minimum", "stdout": "2.1.219 (Claude Code)\n", "exit_code": 0},
        {"name": "pinned-cli", "stdout": "2.1.316 (Claude Code)\n", "exit_code": 0},
        {"name": "too-old", "stdout": "2.1.218 (Claude Code)\n", "exit_code": 0},
        {
            "name": "minimum-prerelease",
            "stdout": "2.1.219rc1 (Claude Code)\n",
            "exit_code": 0,
        },
        {
            "name": "malformed-output",
            "stdout": "not a Claude version\n",
            "exit_code": 0,
        },
        {
            "name": "invalid-version",
            "stdout": "invalid (Claude Code)\n",
            "exit_code": 0,
        },
        {"name": "failed-probe", "stdout": "2.1.316 (Claude Code)\n", "exit_code": 1},
        {"name": "spawn-refused", "stdout": "", "exit_code": 0, "failure": "spawn"},
        {"name": "probe-timeout", "stdout": "", "exit_code": 0, "failure": "timeout"},
    ]
    spec: dict[str, Any] = {
        "operation": "versions",
        "cases": cases,
        "executable": "/synthetic/unit18/claude",
    }
    expected = _python_versions(cases, spec["executable"])
    actual = _node(spec, tmp_path)
    report = {
        "case": "unit18-cli-version-errors",
        "inputs": spec,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "python_boundary": "ensure_cli_compatible with synthetic process responses",
        "typescript_boundary": (
            "pure validator and check-error factory; actual spawn path covered by focused tests"
        ),
        "provider_processes_started": 0,
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit18-cli-version-errors.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected


def _python_catalogs(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import dataclasses
    from unittest.mock import AsyncMock

    from planner.conversation.backends import claude_model_catalog

    async def forbidden(*arguments: Any, **keywords: Any) -> Any:
        raise AssertionError("Catalog transform comparison must not start a process")

    async def run() -> list[dict[str, Any]]:
        results = []
        for case in cases:

            class FakeClient:
                def __init__(
                    self, *arguments: Any, _info: Any = case["info"], **keywords: Any
                ) -> None:
                    self.info = _info

                async def __aenter__(self) -> FakeClient:
                    return self

                async def __aexit__(self, *arguments: Any) -> None:
                    pass

                async def get_server_info(self) -> Any:
                    return self.info

            with (
                patch.object(asyncio, "create_subprocess_exec", forbidden),
                patch.object(claude_model_catalog, "ensure_cli_compatible", AsyncMock()),
                patch.object(claude_model_catalog, "ClaudeSDKClient", FakeClient),
            ):
                try:
                    result = await claude_model_catalog.probe_claude_model_catalog(
                        "/synthetic/claude"
                    )
                    plain = json.loads(json.dumps(dataclasses.asdict(result)))
                except claude_model_catalog.ClaudeModelCatalogUnavailable as error:
                    plain = {"error": str(error)}
                except TypeError as error:
                    plain = {"error_type": type(error).__name__}
            results.append({"name": case["name"], "result": plain})
        return results

    return asyncio.run(run())


def test_unit18_model_catalog(tmp_path: Path, request: pytest.FixtureRequest) -> None:
    """Protect installed-CLI catalog identity and ordered effort choices across languages."""
    started = time.perf_counter()
    cases = [
        {
            "name": "resolved-models-and-effort-order",
            "info": {
                "models": [
                    None,
                    {"value": "default", "resolvedModel": "claude-opus-5[1m]"},
                    {"value": "invalid-no-resolved-id"},
                    {
                        "value": "haiku",
                        "resolvedModel": "claude-haiku-4-5-20251001",
                        "supportedEffortLevels": ["low", "medium"],
                    },
                    {
                        "value": "haiku-alias",
                        "resolvedModel": "claude-haiku-4-5-20251001",
                        "supportedEffortLevels": ["high", "low"],
                    },
                    {
                        "value": "opus",
                        "resolvedModel": "claude-opus-5[1m]",
                        "displayName": "",
                        "supportedEffortLevels": ["max", 9, "high"],
                    },
                ],
            },
        },
        {
            "name": "unmatched-default",
            "info": {
                "models": [
                    {"value": "default", "resolvedModel": "claude-absent"},
                    {
                        "value": "custom",
                        "resolvedModel": "custom-id",
                        "displayName": "Custom",
                    },
                ]
            },
        },
        {"name": "missing-models", "info": {}},
        {"name": "non-list-models", "info": {"models": {}}},
        {"name": "no-models", "info": {"models": []}},
    ]
    spec = {"operation": "catalogs", "cases": cases}
    expected = _python_catalogs(cases)
    actual = _node(spec, tmp_path)
    report = {
        "case": "unit18-model-catalog",
        "inputs": spec,
        "python": expected,
        "typescript": actual,
        "seconds": time.perf_counter() - started,
        "python_boundary": "probe with fake SDK handshake and compatibility check",
        "typescript_boundary": "claude_model_catalog_from_init transform",
        "provider_processes_started": 0,
        "normalization_rules": [],
    }
    destination = request.config.getoption("--compare-report-dir")
    if destination:
        path = Path(destination)
        path.mkdir(parents=True, exist_ok=True)
        (path / "unit18-model-catalog.json").write_text(json.dumps(report, indent=2) + "\n")
    assert actual == expected
