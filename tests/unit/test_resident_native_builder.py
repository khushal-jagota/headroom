"""Scripted native results test adapter decisions, not Mac runtime feasibility."""

from __future__ import annotations

import json
import os
import sys
import zipfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import cast

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
from planner.environments.resident.native_builder import (
    NativeRuntimeBuilder,
    _verify_pure_http_ece,
    run_native_command,
)
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION


@dataclass
class ScriptedNativeCommands:
    runtime: Path
    platform: str = "darwin"
    architecture: str = "arm64"
    version: tuple[int, int, int] = (3, 14, 3)
    macos: str = "15.0"
    implementation: str = "CPython"
    free_threaded: bool = False
    bad_prefix: bool = False
    fail_import: bool = False
    calls: list[tuple[list[str], Path, dict[str, str], float]] = field(default_factory=list)

    def __call__(self, argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> str:
        self.calls.append((argv, cwd, env, timeout))
        if "venv" in argv:
            python = Path(argv[-1]) / "bin/python"
            python.parent.mkdir(parents=True)
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
                    "implementation": self.implementation,
                    "free_threaded": self.free_threaded,
                    "macos": self.macos,
                }
            )
        if "-c" in argv and "importlib.import_module" in argv[4] and self.fail_import:
            raise ResidentError("offline import failed")
        if "wheel" in argv:
            wheels = Path(argv[argv.index("--wheel-dir") + 1])
            (wheels / "planner-2.0.0-py3-none-any.whl").write_bytes(b"test wheel")
        if "-c" in argv and "importlib.metadata.distributions" in argv[4]:
            return json.dumps(
                {"distribution": "planner", "version": "2.0.0", "files": {"bin/panels": "a" * 64}}
            )
        return ""


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, ResidentPackage]:
    app, runtime = tmp_path / "app", tmp_path / "permanent runtime"
    app.mkdir()
    runtime.mkdir()
    (app / "executor.txt").write_text("example==1.2.3\n")
    (app / "requirements-build.txt").write_text("setuptools==80.9.0\nwheel==0.46.3\n")
    python = tmp_path / "absolute python"
    python.write_text("#!/bin/sh\nexit 0\n")
    python.chmod(0o755)
    package = ResidentPackage(
        "a" * 40,
        PROTOCOL_VERSION,
        SPOOL_FORMAT_VERSION,
        "darwin",
        "arm64",
        "3.14.3",
        ExecutorSourceClosure(
            ("src/offline.py", "executor.txt", "requirements-build.txt"), "offline", "executor.txt"
        ),
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
    commands = [call[0] for call in runner.calls]
    downloads = [cmd for cmd in commands if "download" in cmd]
    assert len(downloads) == 1
    assert "--only-binary=:all:" in downloads[0] and "--no-deps" in downloads[0]
    builds = [cmd for cmd in commands if "wheel" in cmd]
    assert len(builds) == 1 and "--no-build-isolation" in builds[0]
    assert builds[0][-1] != str(app)
    permanent_creation = commands.index([str(python), "-I", "-B", "-m", "venv", str(runtime)])
    assert next(i for i, cmd in enumerate(commands) if "check" in cmd) < permanent_creation
    imports = [cmd for cmd in commands if "-c" in cmd and "importlib.import_module" in cmd[4]]
    assert len(imports) == 1 and imports[0][-1] == "offline"
    assert "sys.path.insert" not in imports[0][4]
    receipt = json.loads((runtime / "resident-installed.json").read_text())
    assert receipt["wheel_filename"] == "planner-2.0.0-py3-none-any.whl"
    assert receipt["entrypoint_module"] == "offline"
    assert (runtime / "wheels" / receipt["wheel_filename"]).is_file()
    for _, _, environment, deadline in runner.calls:
        assert environment["HOME"] == "/Users/Khushal"
        assert environment["CODEX_HOME"] == "/Users/Khushal/.codex"
        assert environment["CLAUDE_CONFIG_DIR"] == "/Users/Khushal/.claude"
        assert not any(key.startswith("PLAN_") for key in environment)
        assert "PYTHONPATH" not in environment and "PIP_TARGET" not in environment
        assert "/live/server" not in environment.get("PATH", "")
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
    with pytest.raises(ResidentError, match="CPython|probe"):
        NativeRuntimeBuilder(python, runner=runner)(
            app, runtime, replace(package, python_version="3.14.2")
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
    assert "--require-hashes" in next(call[0] for call in runner.calls if "download" in call[0])


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


@pytest.mark.parametrize(
    "architecture,node_architecture", [("x86_64", "x64"), ("aarch64", "arm64")]
)
def test_linux_node_architecture_matches_native_python(
    tmp_path: Path, architecture: str, node_architecture: str
) -> None:
    app, runtime, python, package, node, npm = _node_inputs(tmp_path)
    runner = ScriptedNodeCommands(
        runtime,
        platform="linux",
        architecture=architecture,
        node_platform="linux",
        node_architecture=node_architecture,
    )
    NativeRuntimeBuilder(python, npm=npm, node=node, runner=runner)(
        app, runtime, replace(package, target_os="linux", target_architecture=architecture)
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


@pytest.mark.parametrize("change", ["intel", "old_macos", "free_threaded", "pypy"])
def test_unsupported_mac_target_refuses_before_any_filesystem_mutation(
    tmp_path: Path, change: str
) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    runtime.rmdir()
    runner = ScriptedNativeCommands(runtime)
    if change == "intel":
        package = replace(package, target_architecture="x86_64")
    elif change == "old_macos":
        runner.macos = "10.15.7"
    elif change == "free_threaded":
        runner.free_threaded = True
    else:
        runner.implementation = "PyPy"
    with pytest.raises(ResidentError):
        NativeRuntimeBuilder(python, runner=runner).preflight(app, package)
    assert not runtime.exists()
    assert not any("venv" in call[0] for call in runner.calls)


@pytest.mark.parametrize("failure", ["download", "check"])
def test_dependency_tag_or_closure_failure_does_not_mutate_permanent_runtime(
    tmp_path: Path, failure: str
) -> None:
    app, runtime, python, package = _inputs(tmp_path)
    scripted = ScriptedNativeCommands(runtime)

    def runner(argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> str:
        if failure in argv:
            raise ResidentError("dependency qualification failed")
        return scripted(argv, cwd, env, timeout)

    with pytest.raises(ResidentError, match="qualification"):
        NativeRuntimeBuilder(python, runner=runner)(app, runtime, package)
    assert list(runtime.iterdir()) == []


@pytest.mark.parametrize("defect", ["none", "native", "version", "tag", "purelib"])
def test_only_exact_pure_http_ece_source_artifact_is_accepted(tmp_path: Path, defect: str) -> None:
    wheel = tmp_path / "http_ece-1.2.1-py2.py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        version = "2.0.0" if defect == "version" else "1.2.1"
        archive.writestr(
            "http_ece-1.2.1.dist-info/METADATA", f"Name: http-ece\nVersion: {version}\n"
        )
        tag = "cp314-cp314-macosx_11_0_arm64" if defect == "tag" else "py3-none-any"
        pure = "false" if defect == "purelib" else "true"
        archive.writestr("http_ece-1.2.1.dist-info/WHEEL", f"Root-Is-Purelib: {pure}\nTag: {tag}\n")
        if defect == "native":
            archive.writestr("http_ece/native.so", "native")
    if defect == "none":
        _verify_pure_http_ece(wheel)
    else:
        with pytest.raises(ResidentError, match="pure Python wheel"):
            _verify_pure_http_ece(wheel)


@pytest.mark.parametrize("fail_npm", [False, True])
def test_npm_configuration_is_distinct_empty_and_scoped_to_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_npm: bool
) -> None:
    from planner.environments.resident.contracts import NativeBuildFailure, NativeCommandFailure

    app, runtime, python, package, node, npm = _node_inputs(tmp_path)
    for key in ("NPM_CONFIG_REGISTRY", "npm_config_userconfig", "npm_config_globalconfig"):
        monkeypatch.setenv(key, "credential-sentinel")
    monkeypatch.setenv("NpM_CoNfIg_Registry", "mixed-case-credential-sentinel")
    scripted = ScriptedNodeCommands(runtime)
    configurations: list[Path] = []

    def runner(argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> str:
        if "--eval" in argv or (
            argv[0] == str(python) and "-c" in argv and "platform.machine()" in argv[4]
        ):
            if not configurations:
                assert "NPM_CONFIG_USERCONFIG" not in env
                assert "NPM_CONFIG_GLOBALCONFIG" not in env
        if "ci" in argv:
            assert (runtime / "bin/python").is_file()
            assert not any("credential-sentinel" in value for value in env.values())
            assert "NpM_CoNfIg_Registry" not in env
            configurations.extend(
                Path(env[key]) for key in ("NPM_CONFIG_USERCONFIG", "NPM_CONFIG_GLOBALCONFIG")
            )
            assert configurations[0] != configurations[1]
            for configuration in configurations:
                assert configuration.is_absolute() and configuration.is_file()
                assert not configuration.is_symlink() and configuration.read_bytes() == b""
                assert configuration.parent.name.startswith("panels-wheel-")
            if fail_npm:
                raise NativeCommandFailure("nonzero_exit", 17)
        return scripted(argv, cwd, env, timeout)

    builder = NativeRuntimeBuilder(python, npm=npm, node=node, runner=runner)
    if fail_npm:
        with pytest.raises(NativeBuildFailure) as failure:
            builder(app, runtime, package)
        assert failure.value.diagnostic.phase == "backend_npm_install"
        assert failure.value.diagnostic.operation == "npm_ci"
        assert failure.value.diagnostic.exit_status == 17
        assert (runtime / "bin/python").is_file()
        assert not (runtime / "resident-installed.json").exists()
        before = len(scripted.calls)
        with pytest.raises(ResidentError, match="fresh empty"):
            builder(app, runtime, package)
        assert len(scripted.calls) == before
    else:
        builder(app, runtime, package)
        assert (runtime / "resident-installed.json").is_file()
    assert configurations and all(not path.exists() for path in configurations)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_runner_rejects_output_overflow_without_stream_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: str
) -> None:
    from planner.environments.resident import native_builder
    from planner.environments.resident.contracts import NativeCommandFailure

    monkeypatch.setattr(native_builder, f"NATIVE_{stream.upper()}_LIMIT_BYTES", 1024)
    script = f"import sys;sys.{stream}.write('credential-sentinel' * 1000)"
    with pytest.raises(NativeCommandFailure) as failure:
        run_native_command([sys.executable, "-c", script], tmp_path, dict(os.environ), 2)
    assert failure.value.kind == "output_limit"
    assert "credential" not in str(failure.value)


def test_runner_start_failure_is_safe_and_typed(tmp_path: Path) -> None:
    from planner.environments.resident.contracts import NativeCommandFailure

    with pytest.raises(NativeCommandFailure) as failure:
        run_native_command(["/missing/credential-sentinel"], tmp_path, {}, 1)
    assert failure.value.kind == "process_start" and failure.value.exit_status is None
    assert "credential" not in str(failure.value)


def test_owned_descendant_holding_pipes_cannot_extend_deadline(tmp_path: Path) -> None:
    import time

    from planner.environments.resident.contracts import NativeCommandFailure

    script = (
        "import subprocess,sys;subprocess.Popen([sys.executable,'-c',"
        "'import time;time.sleep(60)']);print('credential-sentinel')"
    )
    started = time.monotonic()
    with pytest.raises(NativeCommandFailure) as failure:
        run_native_command([sys.executable, "-c", script], tmp_path, dict(os.environ), 0.2)
    elapsed = time.monotonic() - started
    assert failure.value.kind == "timeout"
    assert elapsed < 2.7
    assert "credential" not in str(failure.value)


def test_pipe_read_failure_still_reaps_owned_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def start(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = cast("subprocess.Popen[bytes]", popen(*args, **kwargs))  # type: ignore[call-overload]
        processes.append(process)
        return process

    real_read = os.read

    def broken_read(descriptor: int, size: int) -> bytes:
        # Popen itself reads its exec-error pipe before returning the process.
        if processes:
            raise OSError("credential-sentinel")
        return real_read(descriptor, size)

    monkeypatch.setattr(subprocess, "Popen", start)
    monkeypatch.setattr(os, "read", broken_read)
    with pytest.raises(ResidentError, match="could not be observed") as failure:
        run_native_command(
            [sys.executable, "-c", "import time;print('ready',flush=True);time.sleep(60)"],
            tmp_path,
            dict(os.environ),
            2,
        )
    assert len(processes) == 1 and processes[0].poll() is not None
    assert "credential" not in str(failure.value)


def test_cleanup_deadline_reaps_direct_child_when_escaped_descendant_holds_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import signal
    import subprocess
    import time

    from planner.environments.resident.contracts import NativeCommandFailure

    processes: list[subprocess.Popen[bytes]] = []
    popen = subprocess.Popen

    def start(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
        process = cast("subprocess.Popen[bytes]", popen(*args, **kwargs))  # type: ignore[call-overload]
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", start)
    escaped_pid_file = tmp_path / "escaped-pid"
    script = (
        "import pathlib,subprocess,sys;child=subprocess.Popen([sys.executable,'-c',"
        "'import time;time.sleep(60)'],start_new_session=True);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
    )
    started = time.monotonic()
    try:
        with pytest.raises(NativeCommandFailure) as failure:
            run_native_command(
                [sys.executable, "-c", script, str(escaped_pid_file)],
                tmp_path,
                dict(os.environ),
                0.2,
            )
        assert failure.value.kind == "cleanup_deadline"
        assert time.monotonic() - started < 2.7
        assert len(processes) == 1 and processes[0].returncode == 0
        # No waitable direct child may be abandoned, even when pipe EOF is uncertain.
        with pytest.raises(ChildProcessError):
            os.waitpid(processes[0].pid, os.WNOHANG)
        escaped_pid = int(escaped_pid_file.read_text())
        os.kill(escaped_pid, 0)  # Runner must not signal the escaped session.
    finally:
        if escaped_pid_file.exists():
            try:
                os.kill(int(escaped_pid_file.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass
