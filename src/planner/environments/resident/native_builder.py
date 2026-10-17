"""Build a declared executor closure in its permanent native-host runtime.

The interpreter probe is the authority for platform and version. This adapter
never substitutes a Linux result for a Mac build and never starts Panels' server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from planner.environments.resident.configuration import (
    NATIVE_BUILD_TIMEOUT_SECONDS,
    NATIVE_NODE_BUILD_TIMEOUT_SECONDS,
    NATIVE_PROBE_TIMEOUT_SECONDS,
)
from planner.environments.resident.contracts import ResidentError, ResidentPackage
from planner.environments.resident.packaging import validate_package

NativeCommandRunner = Callable[[list[str], Path, dict[str, str], float], str]

_PROBE = (
    "import json,platform,sys; "
    "print(json.dumps({'platform':sys.platform,'architecture':platform.machine(),"
    "'version':list(sys.version_info[:3]),'prefix':sys.prefix,'executable':sys.executable}))"
)


def run_native_command(
    argv: list[str], cwd: Path, environment: dict[str, str], timeout_seconds: float
) -> str:
    """Execute one bounded command without a shell or inherited server context."""
    try:
        with subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        ) as process:
            try:
                stdout, _ = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                raise
            if process.returncode:
                raise ResidentError(f"native runtime command failed: {Path(argv[0]).name}")
            return stdout
    except (OSError, subprocess.SubprocessError) as exc:
        # Dependency output can contain registry credentials; do not echo it.
        raise ResidentError(f"native runtime command failed: {Path(argv[0]).name}") from exc


def _environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PIP_", "NPM_CONFIG_", "npm_config_"))
        and key
        not in {
            "PYTHONHOME",
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "__PYVENV_LAUNCHER__",
            "NODE_OPTIONS",
            "NODE_PATH",
        }
    }
    environment.update(
        PYTHONDONTWRITEBYTECODE="1",
        NPM_CONFIG_USERCONFIG=os.devnull,
        NPM_CONFIG_GLOBALCONFIG=os.devnull,
    )
    return environment


def _executable(path: Path, description: str) -> None:
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        raise ResidentError(f"{description} must be a concrete absolute executable")


def _requirements(path: Path) -> bool:
    hashed: list[bool] = []
    logical = path.read_text().replace("\\\n", " ")
    for raw in logical.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if (
            re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?"
                r"==[A-Za-z0-9][A-Za-z0-9.!+_-]*"
                r"(?:\s+--hash=sha256:[0-9a-f]{64})*",
                line,
            )
            is None
        ):
            raise ResidentError("Python lock requires exact pins with optional SHA256 hashes")
        hashed.append("--hash=" in line)
    if any(hashed) and not all(hashed):
        raise ResidentError("Python lock cannot mix hashed and unhashed dependencies")
    return bool(hashed) and all(hashed)


def _node_lock(path: Path) -> None:
    try:
        lock = json.loads(path.read_text())
        if not isinstance(lock, dict) or lock.get("lockfileVersion") not in {2, 3}:
            raise ValueError("unsupported lock")
        packages = lock.get("packages")
        if not isinstance(packages, dict) or "" not in packages:
            raise ValueError("missing packages")
        for name, item in packages.items():
            if not name:
                continue
            if not isinstance(item, dict):
                raise ValueError("invalid package")
            resolved, integrity = item.get("resolved"), item.get("integrity")
            if (
                item.get("link")
                or not isinstance(resolved, str)
                or not resolved.startswith("https://")
                or not isinstance(integrity, str)
                or re.fullmatch(r"sha(?:256|512)-[A-Za-z0-9+/]+={0,2}", integrity) is None
            ):
                raise ValueError("unlocked dependency")
    except (OSError, ValueError, TypeError) as exc:
        raise ResidentError("backend lock must contain integrity-locked registry packages") from exc


@dataclass(frozen=True)
class NativeRuntimeBuilder:
    """A production RuntimeBuilder; call only on the intended native Mac host."""

    python: Path
    npm: Path | None = None
    node: Path | None = None
    runner: NativeCommandRunner = run_native_command

    def _probe(
        self,
        python: Path,
        app: Path,
        environment: dict[str, str],
        package: ResidentPackage,
        runtime: Path | None = None,
    ) -> None:
        output = self.runner(
            [str(python), "-I", "-B", "-c", _PROBE],
            app,
            environment,
            NATIVE_PROBE_TIMEOUT_SECONDS,
        )
        try:
            observed = json.loads(output)
            if not isinstance(observed, dict):
                raise ValueError("invalid probe")
            version = observed.get("version")
            if (
                not isinstance(version, list)
                or len(version) != 3
                or any(type(part) is not int for part in version)
            ):
                raise ValueError("invalid version")
            requested = [int(part) for part in package.python_version.split(".")]
            if (
                observed.get("platform") != package.target_os
                or observed.get("architecture") != package.target_architecture
                or version[: len(requested)] != requested
                or observed.get("executable") != str(python)
                or (runtime is not None and observed.get("prefix") != str(runtime))
            ):
                raise ValueError("platform, version or runtime path mismatch")
        except (ValueError, TypeError) as exc:
            raise ResidentError("native Python probe does not match requested runtime") from exc

    def _probe_node(self, app: Path, environment: dict[str, str], package: ResidentPackage) -> None:
        assert self.node is not None
        output = self.runner(
            [
                str(self.node),
                "--eval",
                "console.log(JSON.stringify({platform:process.platform,"
                "architecture:process.arch,version:process.versions.node,executable:process.execPath}))",
            ],
            app,
            environment,
            NATIVE_PROBE_TIMEOUT_SECONDS,
        )
        try:
            observed = json.loads(output)
            if not isinstance(observed, dict):
                raise ValueError("invalid probe")
            version = observed.get("version")
            executable = observed.get("executable")
            if (
                observed.get("platform") != package.target_os
                or observed.get("architecture")
                != {"arm64": "arm64", "x86_64": "x64"}[package.target_architecture]
                or not isinstance(version, str)
                or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None
                or int(version.split(".")[0]) < 22
                or not isinstance(executable, str)
                or Path(executable).resolve() != self.node.resolve()
            ):
                raise ValueError("Node platform, version or executable mismatch")
        except (ValueError, TypeError) as exc:
            raise ResidentError("native Node probe does not match requested runtime") from exc

    def __call__(self, app: Path, runtime: Path, package: ResidentPackage) -> None:
        validate_package(package)
        if not app.is_absolute() or not runtime.is_absolute():
            raise ResidentError("native app and permanent runtime paths must be absolute")
        for path in (app, runtime):
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise ResidentError("native build paths must not contain symlinks")
        if not runtime.is_dir() or any(runtime.iterdir()):
            raise ResidentError("native runtime must be a fresh empty permanent directory")
        _executable(self.python, "native Python")
        requirements = app / package.closure.python_requirements
        if not requirements.is_file() or requirements.is_symlink():
            raise ResidentError("native Python dependency lock is missing or not regular")
        hashed = _requirements(requirements)
        if package.closure.node_package_roots:
            if self.node is None or self.npm is None:
                raise ResidentError("backend roots require explicit absolute native Node and npm")
            _executable(self.node, "native Node")
            _executable(self.npm, "native npm")
            if self.npm.resolve().suffix != ".js":
                raise ResidentError("native npm must resolve to the npm JavaScript entrypoint")
            for root in package.closure.node_package_roots:
                _node_lock(app / root / "package-lock.json")
        environment = _environment()
        environment["PATH"] = os.pathsep.join(
            dict.fromkeys(
                (
                    str(runtime / "bin"),
                    str(self.python.parent),
                    "/usr/bin",
                    "/bin",
                    "/usr/sbin",
                    "/sbin",
                )
            )
        )
        self._probe(self.python, app, environment, package)
        if package.closure.node_package_roots:
            self._probe_node(app, environment, package)
        self.runner(
            [str(self.python), "-I", "-B", "-m", "venv", str(runtime)],
            app,
            environment,
            NATIVE_BUILD_TIMEOUT_SECONDS,
        )
        python = runtime / "bin/python"
        _executable(python, "created runtime Python")
        self._probe(python, app, environment, package, runtime)
        self.runner(
            [
                str(python),
                "-I",
                "-B",
                "-m",
                "pip",
                "--isolated",
                "install",
                *(["--require-hashes"] if hashed else []),
                "--no-deps",
                "--disable-pip-version-check",
                "--requirement",
                str(requirements),
            ],
            app,
            environment,
            NATIVE_BUILD_TIMEOUT_SECONDS,
        )
        if package.closure.node_package_roots:
            assert self.node is not None and self.npm is not None
            (runtime / "bin/node").symlink_to(self.node.resolve())
            for root in package.closure.node_package_roots:
                target = runtime / "node" / root
                for name in package.closure.files:
                    if name.startswith(root + "/"):
                        source = app / name
                        if source.is_symlink() or not source.is_file():
                            raise ResidentError("declared backend source must be a regular file")
                        destination = target / Path(name).relative_to(root)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                self.runner(
                    [str(self.node), str(self.npm.resolve()), "ci", "--no-audit", "--no-fund"],
                    target,
                    environment,
                    NATIVE_NODE_BUILD_TIMEOUT_SECONDS,
                )
            if "agent_backends" in package.closure.node_package_roots:
                _executable(
                    runtime
                    / "node/agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
                    "managed Claude",
                )
        # Import checks the offline executor and its dependencies without running
        # __main__, starting services, opening credentials or contacting providers.
        self.runner(
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import importlib,sys; sys.path.insert(0,sys.argv[1]); "
                "importlib.import_module(sys.argv[2])",
                str(app / "src"),
                package.closure.entrypoint_module,
            ],
            app,
            environment,
            NATIVE_PROBE_TIMEOUT_SECONDS,
        )
