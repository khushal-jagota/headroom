"""Scripted native results test adapter decisions, not Mac runtime feasibility."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from planner.environments.resident.configuration import (
    NATIVE_BUILD_TIMEOUT_SECONDS,
    NATIVE_NODE_BUILD_TIMEOUT_SECONDS,
    NATIVE_PROBE_TIMEOUT_SECONDS,
)
from planner.environments.resident.contracts import (
    ExecutorSourceClosure,
    ResidentError,
    ResidentPackage,
)
from planner.environments.resident.native_builder import NativeRuntimeBuilder, run_native_command
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION


@dataclass
class ScriptedNativeCommands:
    runtime: Path
    platform: str = "darwin"
    architecture: str = "arm64"
    version: tuple[int, int, int] = (3, 12, 9)
    bad_prefix: bool = False
    fail_import: bool = False
    calls: list[tuple[list[str], Path, dict[str, str], float]] = field(default_factory=list)

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> str:
        self.calls.append((argv, cwd, env, timeout))
        if "venv" in argv:
            python = self.runtime / "bin/python"
            python.parent.mkdir()
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
        if "-c" in argv and "platform.machine()" in argv[4]:
            return json.dumps(
                {
                    "platform": self.platform,
                    "architecture": self.architecture,
                    "version": self.version,
                    "prefix": "wrong" if self.bad_prefix else str(self.runtime),
                    "executable": argv[0],
                }
            )
        if "-c" in argv and "importlib.import_module" in argv[4] and self.fail_import:
            raise ResidentError("offline import failed")
        return ""


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, ResidentPackage]:
    app, runtime = tmp_path / "app", tmp_path / "permanent runtime"
    app.mkdir()
    runtime.mkdir()
    (app / "executor.txt").write_text("example==1.2.3\n")
    python = tmp_path / "absolute python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    package = ResidentPackage(
        "a" * 40,
        PROTOCOL_VERSION,
        SPOOL_FORMAT_VERSION,
        "darwin",
        "arm64",
        "3.12",
        ExecutorSourceClosure(("src/offline.py", "executor.txt"), "offline", "executor.txt"),
    )
    return app, runtime, python, package


def test_builds_venv_in_place_installs_only_declared_lock_and_imports_offline_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    monkeypatch.setenv("PATH", "/live/server/.venv/bin")
    monkeypatch.setenv("HOME", "/Users/Khushal")
    monkeypatch.setenv("CODEX_HOME", "/Users/Khushal/.codex")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/Users/Khushal/.claude")
    for key in ("PLAN_DB_PATH", "PLAN_SERVER_CONTROL_SOCKET", "PYTHONPATH", "PIP_TARGET"):
        monkeypatch.setenv(key, "/live/server")
    runner = ScriptedNativeCommands(runtime)
    NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert len(runner.calls) == 5
    assert runner.calls[1][0] == [str(python), "-I", "-B", "-m", "venv", str(runtime)]
    pip = runner.calls[3][0]
    assert pip[:7] == [
        str(runtime / "bin/python"),
        "-I",
        "-B",
        "-m",
        "pip",
        "--isolated",
        "install",
    ]
    assert "--no-deps" in pip
    assert pip[-2:] == ["--requirement", str(app / "executor.txt")]
    assert "--require-hashes" not in pip
    assert runner.calls[4][0][-2:] == [str(app / "src"), "offline"]
    for _, cwd, environment, deadline in runner.calls:
        assert cwd == app
        assert environment["HOME"] == "/Users/Khushal"
        assert environment["CODEX_HOME"] == "/Users/Khushal/.codex"
        assert environment["CLAUDE_CONFIG_DIR"] == "/Users/Khushal/.claude"
        assert not any(key.startswith("PLAN_") for key in environment)
        assert "PYTHONPATH" not in environment and "PIP_TARGET" not in environment
        assert environment["PATH"].split(os.pathsep)[0] == str(runtime / "bin")
        assert "/live/server" not in environment["PATH"]
        assert deadline in {NATIVE_PROBE_TIMEOUT_SECONDS, NATIVE_BUILD_TIMEOUT_SECONDS}


@pytest.mark.parametrize("claim", ["platform", "architecture", "version"])
def test_wrong_native_interpreter_refuses_before_venv(tmp_path: Path, claim: str) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    runner = ScriptedNativeCommands(runtime)
    if claim == "platform":
        runner.platform = "linux"
    elif claim == "architecture":
        runner.architecture = "x86_64"
    else:
        runner.version = (3, 13, 1)
    with pytest.raises(ResidentError, match="probe"):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert len(runner.calls) == 1 and list(runtime.iterdir()) == []


def test_exact_patch_version_is_enforced(tmp_path: Path) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    runner = ScriptedNativeCommands(runtime)
    with pytest.raises(ResidentError, match="probe"):
        NativeRuntimeBuilder(python, runner=runner)(
            app, runtime, replace(package, python_version="3.12.8")
        )


@pytest.mark.parametrize(
    "lock",
    [
        "example",
        "example>=1.0",
        "-r other.txt",
        "-e .",
        "example @ https://example.invalid/a.whl",
        "example==1.*",
        "--index-url https://example.invalid\nexample==1.0",
    ],
)
def test_unsupported_lock_fails_before_any_command(tmp_path: Path, lock: str) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    (app / "executor.txt").write_text(lock)
    runner = ScriptedNativeCommands(runtime)
    with pytest.raises(ResidentError, match="exact pins"):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert runner.calls == []


def test_optional_hashed_lock_enables_pip_hash_enforcement(tmp_path: Path) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    (app / "executor.txt").write_text("example==1.2.3 \\\n    --hash=sha256:" + "a" * 64 + "\n")
    runner = ScriptedNativeCommands(runtime)
    NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert "--require-hashes" in runner.calls[3][0]


def test_mixed_hashed_lock_refuses_before_any_command(tmp_path: Path) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    (app / "executor.txt").write_text("example==1.2.3 --hash=sha256:" + "a" * 64 + "\nother==1.0\n")
    runner = ScriptedNativeCommands(runtime)
    with pytest.raises(ResidentError, match="mix"):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert runner.calls == []


@pytest.mark.parametrize("failure", ["bad_prefix", "fail_import"])
def test_post_build_validation_failures_are_not_success(tmp_path: Path, failure: str) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    runner = ScriptedNativeCommands(runtime)
    setattr(runner, failure, True)
    with pytest.raises(ResidentError):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert not (runtime / "resident-runtime.json").exists()


def test_backend_build_requires_explicit_native_tools_before_build(tmp_path: Path) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    closure = replace(
        package.closure,
        files=package.closure.files + ("backends/package.json", "backends/package-lock.json"),
        node_package_roots=("backends",),
    )
    runner = ScriptedNativeCommands(runtime)
    with pytest.raises(ResidentError, match="explicit absolute native Node and npm"):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, replace(package, closure=closure))
    assert runner.calls == []


def test_existing_runtime_and_relative_interpreter_refuse(tmp_path: Path) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    runner = ScriptedNativeCommands(runtime)
    with pytest.raises(ResidentError, match="absolute executable"):
        NativeRuntimeBuilder(Path("python"), runner=runner)(app, runtime, package)
    (runtime / "partial").write_text("untrusted")
    with pytest.raises(ResidentError, match="fresh empty"):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert runner.calls == []


def test_missing_lock_and_symlinked_runtime_refuse(tmp_path: Path) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    runner = ScriptedNativeCommands(runtime)
    (app / "executor.txt").unlink()
    with pytest.raises(ResidentError, match="lock is missing"):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    link = tmp_path / "link"
    link.symlink_to(runtime, target_is_directory=True)
    with pytest.raises(ResidentError, match="symlinks"):
        NativeRuntimeBuilder(python, runner=runner)(app, link, package)
    assert runner.calls == []


def test_real_runner_reports_failure_without_command_output(tmp_path: Path) -> None:
    with pytest.raises(ResidentError) as error:
        run_native_command(
            [sys.executable, "-c", "print('credential');raise SystemExit(1)"],
            tmp_path,
            dict(os.environ),
            10,
        )
    assert "credential" not in str(error.value)


def test_real_runner_deadline_refuses(tmp_path: Path) -> None:
    with pytest.raises(ResidentError):
        run_native_command(
            [sys.executable, "-c", "import time;time.sleep(10)"], tmp_path, dict(os.environ), 0.05
        )


def test_real_linux_python_cannot_certify_mac(tmp_path: Path) -> None:
    if sys.platform != "linux":
        pytest.skip("Linux refusal instrument")
    app, runtime, _, package = _inputs(tmp_path)
    with pytest.raises(ResidentError, match="probe"):
        NativeRuntimeBuilder(Path(sys.executable))(app, runtime, package)
    assert list(runtime.iterdir()) == []


def _node_inputs(tmp_path: Path) -> tuple[Path, Path, Path, ResidentPackage, Path, Path]:
    app, runtime, python, package = _inputs(tmp_path)
    root = app / "agent_backends"
    root.mkdir()
    (root / "package.json").write_text('{"dependencies":{"example":"1.0.0"}}')
    (root / "package-lock.json").write_text(
        json.dumps(
            {
                "lockfileVersion": 3,
                "packages": {
                    "": {},
                    "node_modules/example": {
                        "resolved": "https://registry.npmjs.org/example/-/example-1.0.0.tgz",
                        "integrity": "sha512-abcdef==",
                    },
                },
            }
        )
    )
    (root / "declared.js").write_text("// declared source")
    (root / "undeclared.js").write_text("// must not transfer")
    closure = replace(
        package.closure,
        files=package.closure.files
        + (
            "agent_backends/package.json",
            "agent_backends/package-lock.json",
            "agent_backends/declared.js",
        ),
        node_package_roots=("agent_backends",),
    )
    node, npm = tmp_path / "node", tmp_path / "npm-cli.js"
    for executable in (node, npm):
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
    return app, runtime, python, replace(package, closure=closure), node, npm


@dataclass
class ScriptedNodeCommands(ScriptedNativeCommands):
    node_platform: str = "darwin"
    node_architecture: str = "arm64"
    node_version: str = "22.14.0"
    install_claude: bool = True

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> str:
        if "--eval" in argv:
            self.calls.append((argv, cwd, env, timeout))
            return json.dumps(
                {
                    "platform": self.node_platform,
                    "architecture": self.node_architecture,
                    "version": self.node_version,
                    "executable": argv[0],
                }
            )
        if "ci" in argv:
            self.calls.append((argv, cwd, env, timeout))
            if self.install_claude:
                executable = cwd / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
                executable.parent.mkdir(parents=True)
                executable.write_text("provider must never execute")
                executable.chmod(0o755)
            return ""
        return super().__call__(argv, cwd, env, timeout)


def test_native_node_build_copies_only_declared_source_and_checks_claude_without_running_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, runtime, python, package, node, npm = _node_inputs(tmp_path)
    monkeypatch.setenv("NODE_OPTIONS", "--require /live/server/preload.js")
    monkeypatch.setenv("NODE_PATH", "/live/server/node_modules")
    runner = ScriptedNodeCommands(runtime)
    NativeRuntimeBuilder(python, npm=npm, node=node, runner=runner)(app, runtime, package)
    target = runtime / "node/agent_backends"
    assert (target / "declared.js").read_text() == "// declared source"
    assert not (target / "undeclared.js").exists()
    assert not (app / "agent_backends/node_modules").exists()
    assert (runtime / "bin/node").resolve() == node
    npm_calls = [call for call in runner.calls if "ci" in call[0]]
    assert len(npm_calls) == 1
    argv, cwd, env, timeout = npm_calls[0]
    assert argv == [str(node), str(npm), "ci", "--no-audit", "--no-fund"]
    assert cwd == target and timeout == NATIVE_NODE_BUILD_TIMEOUT_SECONDS
    assert "NODE_OPTIONS" not in env and "NODE_PATH" not in env
    assert not any("claude.exe" in argument for call in runner.calls for argument in call[0])


@pytest.mark.parametrize("claim", ["node_platform", "node_architecture", "node_version"])
def test_node_mismatch_refuses_before_native_build(tmp_path: Path, claim: str) -> None:
    app, runtime, python, package, node, npm = _node_inputs(tmp_path)
    runner = ScriptedNodeCommands(runtime)
    setattr(
        runner,
        claim,
        {"node_platform": "linux", "node_architecture": "x64", "node_version": "20.0.0"}[claim],
    )
    with pytest.raises(ResidentError, match="Node probe"):
        NativeRuntimeBuilder(python, npm=npm, node=node, runner=runner)(app, runtime, package)
    assert list(runtime.iterdir()) == []
    assert not any("venv" in argv for argv, _, _, _ in runner.calls)


def test_node_x64_is_target_x86_64(tmp_path: Path) -> None:
    app, runtime, python, package, node, npm = _node_inputs(tmp_path)
    runner = ScriptedNodeCommands(runtime, architecture="x86_64", node_architecture="x64")
    NativeRuntimeBuilder(python, npm=npm, node=node, runner=runner)(
        app, runtime, replace(package, target_architecture="x86_64")
    )


def test_missing_managed_claude_refuses_preparation(tmp_path: Path) -> None:
    app, runtime, python, package, node, npm = _node_inputs(tmp_path)
    runner = ScriptedNodeCommands(runtime, install_claude=False)
    with pytest.raises(ResidentError, match="managed Claude"):
        NativeRuntimeBuilder(python, npm=npm, node=node, runner=runner)(app, runtime, package)


@pytest.mark.parametrize("invalid", ["missing", "unlocked", "wrong_npm"])
def test_node_lock_and_tool_errors_fail_before_any_probe(tmp_path: Path, invalid: str) -> None:
    app, runtime, python, package, node, npm = _node_inputs(tmp_path)
    if invalid == "missing":
        (app / "agent_backends/package-lock.json").unlink()
    elif invalid == "unlocked":
        (app / "agent_backends/package-lock.json").write_text(
            '{"lockfileVersion":3,"packages":{"":{},"node_modules/example":{"link":true}}}'
        )
    else:
        npm = node
    runner = ScriptedNodeCommands(runtime)
    with pytest.raises(ResidentError):
        NativeRuntimeBuilder(python, npm=npm, node=node, runner=runner)(app, runtime, package)
    assert runner.calls == []
