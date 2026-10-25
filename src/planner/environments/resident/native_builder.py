"""Build a declared executor closure in its permanent native-host runtime.

The interpreter probe is the authority for platform and version. This adapter
never substitutes a Linux result for a Mac build and never starts Panels' server.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path

from planner.environments.resident.configuration import (
    NATIVE_BUILD_TIMEOUT_SECONDS,
    NATIVE_CLEANUP_GRACE_SECONDS,
    NATIVE_NODE_BUILD_TIMEOUT_SECONDS,
    NATIVE_PROBE_TIMEOUT_SECONDS,
    NATIVE_STDERR_LIMIT_BYTES,
    NATIVE_STDOUT_LIMIT_BYTES,
)
from planner.environments.resident.contracts import (
    NativeBuildDiagnostic,
    NativeBuildFailure,
    NativeBuildOperation,
    NativeBuildPhase,
    NativeCommandFailure,
    NativeFailureKind,
    ResidentError,
    ResidentPackage,
)
from planner.environments.resident.packaging import validate_package

NativeCommandRunner = Callable[[list[str], Path, dict[str, str], float], str]

_PROBE = (
    "import json,platform,sys; "
    "print(json.dumps({'platform':sys.platform,'architecture':platform.machine(),"
    "'version':list(sys.version_info[:3]),'prefix':sys.prefix,'executable':sys.executable,"
    "'implementation':platform.python_implementation(),'macos':platform.mac_ver()[0],"
    "'free_threaded':bool(__import__('sysconfig').get_config_var('Py_GIL_DISABLED'))}))"
)


_INSTALLED_FILES = """
import hashlib, importlib.metadata, json, pathlib, sys, sysconfig
root = pathlib.Path(sys.argv[1]).resolve()
files = {}
for distribution in importlib.metadata.distributions():
    for item in distribution.files or ():
        path = pathlib.Path(distribution.locate_file(item))
        relative = path.resolve().relative_to(root).as_posix()
        if path.is_symlink() or not path.is_file():
            raise ValueError('installed file is not regular')
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
# RECORD is authoritative for generated scripts. Enumerate package directories too:
# bytecode and unlisted importable files also affect the code Python can execute.
for key in ('purelib', 'platlib'):
    site = pathlib.Path(sysconfig.get_path(key))
    site.resolve().relative_to(root)
    for path in site.rglob('*'):
        if path.is_symlink():
            raise ValueError('installed package contains a symlink')
        if path.is_file():
            files[path.resolve().relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
application = importlib.metadata.distribution('planner')
entrypoints = [e for e in application.entry_points
               if e.group == 'console_scripts' and e.name == 'panels']
if len(entrypoints) != 1 or entrypoints[0].value != 'planner.executor.entrypoint:main':
    raise ValueError('unexpected console entrypoint')
print(json.dumps({'distribution': application.metadata['Name'], 'version': application.version,
                  'files': files}))
"""


def run_native_command(
    argv: list[str], cwd: Path, environment: dict[str, str], timeout_seconds: float
) -> str:
    """Execute one bounded command without a shell or inherited server context."""
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        raise NativeCommandFailure("process_start") from exc
    assert process.stdout is not None and process.stderr is not None
    output = bytearray()
    counts = {process.stdout: 0, process.stderr: 0}
    limits = {process.stdout: NATIVE_STDOUT_LIMIT_BYTES, process.stderr: NATIVE_STDERR_LIMIT_BYTES}
    failure: NativeFailureKind | None = None
    deadline = time.monotonic() + timeout_seconds
    try:
        with selectors.DefaultSelector() as selector:
            for stream in counts:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while True:
                status = process.poll()
                if not selector.get_map() and status is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    failure = "timeout"
                    break
                for key, _ in selector.select(min(remaining, 0.05)):
                    ready_stream = key.fileobj
                    assert ready_stream in counts
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(ready_stream)
                        continue
                    counts[ready_stream] += len(chunk)
                    if counts[ready_stream] > limits[ready_stream]:
                        failure = "output_limit"
                        break
                    if ready_stream is process.stdout:
                        output.extend(chunk)
                if failure is not None:
                    break
            if failure is not None:
                # This command owns the session/process group, including descendants
                # holding its pipes open after the direct child has already exited.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                cleanup_deadline = time.monotonic() + NATIVE_CLEANUP_GRACE_SECONDS
                while True:
                    status = process.poll()
                    if not selector.get_map() and status is not None:
                        break
                    remaining = cleanup_deadline - time.monotonic()
                    if remaining <= 0:
                        process.poll()
                        raise NativeCommandFailure("cleanup_deadline")
                    for key, _ in selector.select(min(remaining, 0.05)):
                        if not os.read(key.fd, 65536):
                            selector.unregister(key.fileobj)
                raise NativeCommandFailure(failure)
        status = process.returncode
        if status:
            raise NativeCommandFailure("nonzero_exit", status)
        return output.decode(errors="replace")
    except NativeCommandFailure:
        raise
    except BaseException as exc:
        # Selector/pipe failures and interrupts must not abandon our owned command.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.stdout.close()
        process.stderr.close()
        try:
            process.wait(timeout=NATIVE_CLEANUP_GRACE_SECONDS)
        except subprocess.TimeoutExpired as cleanup_error:
            raise NativeCommandFailure("cleanup_deadline") from cleanup_error
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ResidentError("native command could not be observed") from exc
    finally:
        process.stdout.close()
        process.stderr.close()


def _environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PLAN_", "PIP_"))
        and not key.lower().startswith("npm_config_")
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
        PATH="/usr/bin:/bin:/usr/sbin:/sbin",
        PYTHONDONTWRITEBYTECODE="1",
    )
    return environment


def _npm_environment(scratch: Path, environment: dict[str, str]) -> dict[str, str]:
    """Distinct empty files exist for the owning scratch directory's lifetime."""
    user, global_config = scratch / "npm-user.conf", scratch / "npm-global.conf"
    user.write_bytes(b"")
    global_config.write_bytes(b"")
    return dict(
        environment,
        NPM_CONFIG_USERCONFIG=str(user.absolute()),
        NPM_CONFIG_GLOBALCONFIG=str(global_config.absolute()),
    )


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


_HTTP_ECE_SOURCE = (
    "https://files.pythonhosted.org/packages/7c/af/"
    "249d1576653b69c20b9ac30e284b63bd94af6a175d72d87813235caf2482/http_ece-1.2.1.tar.gz"
)
_HTTP_ECE_SHA256 = "8c6ab23116bbf6affda894acfd5f2ca0fb8facbcbb72121c11c75c33e7ce8cff"
_DOWNLOAD_PINNED_SOURCE = """
import hashlib, pathlib, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=60) as response:
    source = response.read()
if hashlib.sha256(source).hexdigest() != sys.argv[3]:
    raise ValueError('source digest mismatch')
pathlib.Path(sys.argv[2]).write_bytes(source)
"""


def _verify_pure_http_ece(wheel: Path) -> None:
    """The sole source exception cannot introduce a native build or new package."""
    try:
        if re.fullmatch(r"http_ece-1\.2\.1-(?:py3|py2\.py3)-none-any\.whl", wheel.name) is None:
            raise ValueError("unexpected pure wheel tag")
        with zipfile.ZipFile(wheel) as archive:
            names = archive.namelist()
            if any(
                Path(name).suffix.lower() in {".so", ".dylib", ".pyd", ".dll", ".a", ".o"}
                for name in names
            ):
                raise ValueError("native member")
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            wheel_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
            if len(metadata_names) != 1 or len(wheel_names) != 1:
                raise ValueError("invalid metadata")
            metadata = Parser().parsestr(archive.read(metadata_names[0]).decode())
            wheel_metadata = Parser().parsestr(archive.read(wheel_names[0]).decode())
            if (
                metadata["Name"].replace("-", "_").lower() != "http_ece"
                or metadata["Version"] != "1.2.1"
                or wheel_metadata["Root-Is-Purelib"] != "true"
                or not wheel_metadata.get_all("Tag")
                or any(
                    tag not in {"py2-none-any", "py3-none-any"}
                    for tag in wheel_metadata.get_all("Tag", [])
                )
            ):
                raise ValueError("wrong pure package metadata")
    except (OSError, ValueError, KeyError, AttributeError, zipfile.BadZipFile) as exc:
        raise ResidentError(
            "http_ece source build must produce its exact pure Python wheel"
        ) from exc


@dataclass(frozen=True)
class NativeRuntimeBuilder:
    """A production RuntimeBuilder; call only on the intended native Mac host."""

    python: Path
    npm: Path | None = None
    node: Path | None = None
    runner: NativeCommandRunner = run_native_command

    def _command(
        self,
        phase: NativeBuildPhase,
        operation: NativeBuildOperation,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
    ) -> str:
        try:
            return self.runner(argv, cwd, environment, timeout_seconds)
        except NativeCommandFailure as exc:
            raise NativeBuildFailure(
                NativeBuildDiagnostic(phase, operation, exc.kind, exc.exit_status)
            ) from exc

    def _probe(
        self,
        python: Path,
        app: Path,
        environment: dict[str, str],
        package: ResidentPackage,
        runtime: Path | None = None,
    ) -> None:
        output = self._command(
            "python_probe",
            "python_probe",
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
            if (
                observed.get("implementation") != "CPython"
                or observed.get("free_threaded") is not False
            ):
                raise ValueError("ordinary CPython required")
            if package.target_os == "darwin":
                if package.target_architecture != "arm64":
                    raise ResidentError("Intel macOS is unsupported for the pinned dependency set")
                macos = observed.get("macos")
                if (
                    version != [3, 14, 3]
                    or not isinstance(macos, str)
                    or re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", macos) is None
                    or int(macos.split(".")[0]) < 11
                ):
                    raise ValueError("Mac requires CPython 3.14.3 and macOS 11 or later")
        except (ValueError, TypeError) as exc:
            raise ResidentError("native Python probe does not match requested runtime") from exc

    def _probe_node(self, app: Path, environment: dict[str, str], package: ResidentPackage) -> None:
        assert self.node is not None
        output = self._command(
            "node_probe",
            "node_probe",
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
                != {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64"}[
                    package.target_architecture
                ]
                or not isinstance(version, str)
                or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None
                or int(version.split(".")[0]) < 22
                or not isinstance(executable, str)
                or Path(executable).resolve() != self.node.resolve()
            ):
                raise ValueError("Node platform, version or executable mismatch")
        except (ValueError, TypeError) as exc:
            raise ResidentError("native Node probe does not match requested runtime") from exc

    def preflight(self, app: Path, package: ResidentPackage) -> None:
        """Qualify the requested native host without creating any host files."""
        validate_package(package)
        if not app.is_absolute():
            raise ResidentError("native app path must be absolute")
        if any(parent.is_symlink() for parent in (app, *app.parents)):
            raise ResidentError("native build paths must not contain symlinks")
        if package.target_os == "darwin" and package.target_architecture != "arm64":
            raise ResidentError("Intel macOS is unsupported for the pinned dependency set")
        _executable(self.python, "native Python")
        for name in (package.closure.python_requirements, package.closure.build_requirements):
            lock = app / name
            if not lock.is_file() or lock.is_symlink():
                raise ResidentError("native Python dependency lock is missing or not regular")
            _requirements(lock)
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
        self._probe(self.python, app, environment, package)
        if package.closure.node_package_roots:
            self._probe_node(app, environment, package)

    def __call__(self, app: Path, runtime: Path, package: ResidentPackage) -> None:
        if not runtime.is_absolute():
            raise ResidentError("native permanent runtime path must be absolute")
        if any(parent.is_symlink() for parent in (runtime, *runtime.parents)):
            raise ResidentError("native build paths must not contain symlinks")
        if not runtime.is_dir() or any(runtime.iterdir()):
            raise ResidentError("native runtime must be a fresh empty permanent directory")
        self.preflight(app, package)
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
        requirements = app / package.closure.python_requirements
        build_requirements = app / package.closure.build_requirements
        # Pip evaluates the real interpreter's supported tags and Requires-Python.
        # All downloads and source-generated build files stay outside the immutable app.
        with tempfile.TemporaryDirectory(prefix="panels-wheel-") as temporary:
            scratch = Path(temporary)
            environment = _npm_environment(scratch, environment)
            source, wheels, build = scratch / "source", scratch / "wheels", scratch / "build"
            shutil.copytree(app, source)
            wheels.mkdir()
            self._command(
                "python_build",
                "create_venv",
                [str(self.python), "-I", "-B", "-m", "venv", str(build)],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            build_python = build / "bin/python"
            _executable(build_python, "build Python")
            pip = [str(build_python), "-I", "-B", "-m", "pip", "--isolated"]
            self._command(
                "python_build",
                "install_build_tools",
                [
                    *pip,
                    "install",
                    "--no-deps",
                    "--only-binary=:all:",
                    "--disable-pip-version-check",
                    *(["--require-hashes"] if _requirements(build_requirements) else []),
                    "--requirement",
                    str(build_requirements),
                ],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            dependency_provenance: list[dict[str, str]] = []
            download_requirements = requirements
            logical_lines = requirements.read_text().replace("\\\n", " ").splitlines()
            source_lines = [
                line
                for line in logical_lines
                if re.match(r"(?i)^http[-_]ece==1\.2\.1(?:\s|$)", line.strip())
            ]
            if source_lines:
                download_requirements = scratch / "binary-requirements.txt"
                download_requirements.write_text(
                    "\n".join(line for line in logical_lines if line not in source_lines) + "\n"
                )
                archive = scratch / "http_ece-1.2.1.tar.gz"
                self._command(
                    "python_build",
                    "download_source",
                    [
                        str(build_python),
                        "-I",
                        "-B",
                        "-c",
                        _DOWNLOAD_PINNED_SOURCE,
                        _HTTP_ECE_SOURCE,
                        str(archive),
                        _HTTP_ECE_SHA256,
                    ],
                    app,
                    environment,
                    NATIVE_BUILD_TIMEOUT_SECONDS,
                )
                if hashlib.sha256(archive.read_bytes()).hexdigest() != _HTTP_ECE_SHA256:
                    raise ResidentError("http_ece source digest mismatch")
                self._command(
                    "python_build",
                    "build_source_wheel",
                    [
                        *pip,
                        "wheel",
                        "--no-deps",
                        "--no-build-isolation",
                        "--no-index",
                        "--wheel-dir",
                        str(wheels),
                        str(archive),
                    ],
                    scratch,
                    environment,
                    NATIVE_BUILD_TIMEOUT_SECONDS,
                )
                pure_wheels = list(wheels.glob("http_ece-*.whl"))
                if len(pure_wheels) != 1:
                    raise ResidentError("http_ece source build must produce one wheel")
                _verify_pure_http_ece(pure_wheels[0])
                dependency_provenance.append(
                    {
                        "source_url": _HTTP_ECE_SOURCE,
                        "source_sha256": _HTTP_ECE_SHA256,
                        "wheel_filename": pure_wheels[0].name,
                        "wheel_sha256": hashlib.sha256(pure_wheels[0].read_bytes()).hexdigest(),
                    }
                )
            self._command(
                "python_build",
                "download_dependencies",
                [
                    *pip,
                    "download",
                    "--no-deps",
                    "--only-binary=:all:",
                    "--disable-pip-version-check",
                    *(["--require-hashes"] if _requirements(requirements) else []),
                    "--dest",
                    str(wheels),
                    "--requirement",
                    str(download_requirements),
                ],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            self._command(
                "python_build",
                "build_application_wheel",
                [
                    *pip,
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--no-index",
                    "--disable-pip-version-check",
                    "--wheel-dir",
                    str(wheels),
                    str(source),
                ],
                source,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            application_wheels = list(wheels.glob("planner-*.whl"))
            if len(application_wheels) != 1:
                raise ResidentError("build must produce one planner wheel")
            application_wheel = application_wheels[0]
            artifacts = sorted(wheels.glob("*.whl"))
            # Use a separate scratch venv: build tools must not hide an incomplete
            # runtime lock during the dependency check.
            validation = scratch / "validation"
            self._command(
                "python_build",
                "create_venv",
                [str(self.python), "-I", "-B", "-m", "venv", str(validation)],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            validation_pip = [str(validation / "bin/python"), "-I", "-B", "-m", "pip", "--isolated"]
            self._command(
                "python_build",
                "install_wheels",
                [*validation_pip, "install", "--no-index", "--no-deps", *map(str, artifacts)],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            self._command(
                "python_build",
                "check_dependencies",
                [*validation_pip, "check"],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            self._command(
                "python_build",
                "create_venv",
                [str(self.python), "-I", "-B", "-m", "venv", str(runtime)],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            python = runtime / "bin/python"
            _executable(python, "created runtime Python")
            self._probe(python, app, environment, package, runtime)
            self._command(
                "runtime_verification",
                "install_wheels",
                [
                    str(python),
                    "-I",
                    "-B",
                    "-m",
                    "pip",
                    "--isolated",
                    "install",
                    "--no-index",
                    "--no-deps",
                    *map(str, artifacts),
                ],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            self._command(
                "runtime_verification",
                "check_dependencies",
                [str(python), "-I", "-B", "-m", "pip", "--isolated", "check"],
                app,
                environment,
                NATIVE_BUILD_TIMEOUT_SECONDS,
            )
            retained = runtime / "wheels" / application_wheel.name
            retained.parent.mkdir()
            shutil.copyfile(application_wheel, retained)
            wheel_digest = hashlib.sha256(retained.read_bytes()).hexdigest()
            if package.closure.node_package_roots:
                assert self.node is not None and self.npm is not None
                (runtime / "bin/node").symlink_to(self.node.resolve())
                for root in package.closure.node_package_roots:
                    target = runtime / "node" / root
                    for name in package.closure.files:
                        if name.startswith(root + "/"):
                            source = app / name
                            if source.is_symlink() or not source.is_file():
                                raise ResidentError(
                                    "declared backend source must be a regular file"
                                )
                            destination = target / Path(name).relative_to(root)
                            destination.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copyfile(source, destination)
                    self._command(
                        "backend_npm_install",
                        "npm_ci",
                        [str(self.node), str(self.npm.resolve()), "ci", "--no-audit", "--no-fund"],
                        target,
                        environment,
                        NATIVE_NODE_BUILD_TIMEOUT_SECONDS,
                    )
                if "agent_backends" in package.closure.node_package_roots:
                    _executable(
                        runtime
                        / "node/agent_backends/node_modules"
                        / "@anthropic-ai/claude-code/bin/claude.exe",
                        "managed Claude",
                    )
        # The isolated interpreter imports only installed packages, never exported source.
        self._command(
            "runtime_verification",
            "import_entrypoint",
            [
                str(python),
                "-I",
                "-B",
                "-c",
                "import importlib,sys; importlib.import_module(sys.argv[1])",
                package.closure.entrypoint_module,
            ],
            app,
            environment,
            NATIVE_PROBE_TIMEOUT_SECONDS,
        )
        installed = self._command(
            "runtime_verification",
            "installed_files",
            [str(python), "-I", "-B", "-c", _INSTALLED_FILES, str(runtime)],
            app,
            environment,
            NATIVE_PROBE_TIMEOUT_SECONDS,
        )
        try:
            identity = json.loads(installed)
            if not isinstance(identity, dict) or not isinstance(identity.get("files"), dict):
                raise ValueError("missing installed files")
            if identity.get("distribution") != "planner" or not identity.get("version"):
                raise ValueError("wrong installed distribution")
            for name, digest in identity["files"].items():
                path = Path(name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not isinstance(digest, str)
                    or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                ):
                    raise ValueError("invalid installed file identity")
            if "bin/panels" not in identity["files"]:
                raise ValueError("missing generated console entrypoint")
        except (ValueError, TypeError) as exc:
            raise ResidentError("installed wheel identity could not be certified") from exc
        identity.update(
            wheel_filename=retained.name,
            wheel_sha256=wheel_digest,
            entrypoint_module=package.closure.entrypoint_module,
            built_dependencies=dependency_provenance,
        )
        (runtime / "resident-installed.json").write_text(
            json.dumps(identity, sort_keys=True) + "\n"
        )
