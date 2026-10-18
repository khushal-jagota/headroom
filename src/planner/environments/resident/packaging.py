"""Exact-source payloads and native, permanent-path runtime preparation.

Setuptools owns Python package discovery. The cold export includes standard
package input roots, while its immutable manifest records exact committed bytes.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import cast

from planner.environments.resident.configuration import MAC_PYTHON_VERSION
from planner.environments.resident.contracts import (
    ExecutorSourceClosure,
    PreparedResidentPackage,
    ResidentError,
    ResidentPackage,
    ResidentPaths,
)
from planner.environments.resident.installed import installed_identity
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION

RuntimeBuilder = Callable[[Path, Path, ResidentPackage], None]
MANIFEST_NAME = "resident-package.json"
RUNTIME_MARKER = "resident-runtime.json"
_GENERATED = {"bin/panels"}
_FORBIDDEN = {"data", ".venv", "node_modules", "web", ".git"}


def _relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or not path.parts
        or path.is_absolute()
        or str(path) != value
        or any(part in {".", "..", *_FORBIDDEN} for part in path.parts)
        or "\\" in value
        or any(ord(char) < 32 for char in value)
    ):
        raise ResidentError(f"unsafe package path: {value!r}")
    return path


def validate_package(package: ResidentPackage) -> None:
    if re.fullmatch(r"[0-9a-f]{40}", package.application_revision) is None:
        raise ResidentError("application revision must be a full lowercase source SHA")
    if (
        package.protocol_version != PROTOCOL_VERSION
        or package.spool_format_version != SPOOL_FORMAT_VERSION
    ):
        raise ResidentError("unsupported executor compatibility versions")
    if package.target_os == "darwin":
        if package.target_architecture != "arm64":
            raise ResidentError("Intel macOS is unsupported for the pinned native dependencies")
        if package.python_version != MAC_PYTHON_VERSION:
            raise ResidentError("Mac executor requires ordinary CPython " + MAC_PYTHON_VERSION)
    elif package.target_os != "linux" or package.target_architecture not in {"aarch64", "x86_64"}:
        raise ResidentError("unsupported native runtime target")
    if re.fullmatch(r"3\.(?:1[2-9]|[2-9][0-9])(?:\.[0-9]+)?", package.python_version) is None:
        raise ResidentError("executor requires an explicit Python >= 3.12 version")
    closure = package.closure
    if not closure.files or len(set(closure.files)) != len(closure.files):
        raise ResidentError("standard package inputs are missing or repeated")
    if (
        any(
            name in {"src/planner/core/server.py", "src/planner/cli/main.py"}
            or "src/planner/cli/main.py".startswith(name + "/")
            for name in closure.files
        )
        and closure.entrypoint_module != "planner.executor.entrypoint"
    ):
        raise ResidentError("full planner source requires the agreed offline executor entrypoint")
    for item in closure.files:
        _relative(item)
        if item in _GENERATED or item == MANIFEST_NAME:
            raise ResidentError("source closure overlaps generated package artifacts")
    if re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", closure.entrypoint_module) is None:
        raise ResidentError("executor offline entrypoint module is missing or invalid")
    module_path = "src/" + closure.entrypoint_module.replace(".", "/")
    if not any(
        name in {module_path + ".py", module_path + "/__main__.py"}
        or module_path.startswith(name + "/")
        for name in closure.files
    ):
        raise ResidentError("executor offline entrypoint is absent from source closure")
    if not isinstance(closure.build_requirements, str):
        raise ResidentError("build dependency lock must be a path")
    _relative(closure.build_requirements)
    if not isinstance(closure.python_requirements, str):
        raise ResidentError("Python dependency lock must be a path")
    _relative(closure.python_requirements)
    if closure.build_requirements not in closure.files:
        raise ResidentError("locked build dependency input is absent from source closure")
    if closure.python_requirements not in closure.files:
        raise ResidentError("locked Python dependency input is absent from source closure")
    for root in closure.node_package_roots:
        _relative(root)
        if any(
            root + "/" + name not in closure.files for name in ("package.json", "package-lock.json")
        ):
            raise ResidentError("backend dependency manifests are absent from source closure")


def package_from_dict(payload: dict[str, object]) -> ResidentPackage:
    try:
        closure = payload["closure"]
        if not isinstance(closure, dict):
            raise ValueError("closure must be an object")
        files = closure["files"]
        roots = closure.get("node_package_roots", [])
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise ValueError("files must be a list of paths")
        if not isinstance(roots, list) or not all(isinstance(item, str) for item in roots):
            raise ValueError("node roots must be a list of paths")
        strings = [
            payload[key]
            for key in (
                "application_revision",
                "target_os",
                "target_architecture",
                "python_version",
            )
        ]
        strings += [closure["entrypoint_module"], closure["python_requirements"]]
        if not all(isinstance(item, str) for item in strings):
            raise ValueError("package identities must be strings")
        protocol, spool = payload["protocol_version"], payload["spool_format_version"]
        if type(protocol) is not int or type(spool) is not int:
            raise ValueError("compatibility versions must be integers")
        package = ResidentPackage(
            application_revision=cast(str, strings[0]),
            protocol_version=protocol,
            spool_format_version=spool,
            target_os=cast(str, strings[1]),
            target_architecture=cast(str, strings[2]),
            python_version=cast(str, strings[3]),
            closure=ExecutorSourceClosure(
                tuple(files),
                cast(str, strings[4]),
                cast(str, strings[5]),
                tuple(roots),
                cast(str, closure.get("build_requirements", "requirements-build.txt")),
            ),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ResidentError(f"invalid resident package specification: {exc}") from exc
    validate_package(package)
    return package


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _safe_parents(path: Path) -> None:
    for candidate in (path.absolute(), *path.absolute().parents):
        if candidate.is_symlink():
            raise ResidentError(f"symlink is not permitted in package paths: {candidate}")


def _source_hashes(app: Path, package: ResidentPackage) -> dict[str, str]:
    inputs: dict[str, str] = {}
    for name in package.closure.files:
        root = app / name
        _safe_parents(root)
        if not root.exists():
            raise ResidentError(f"package input is missing: {name}")
        for path in sorted(root.rglob("*")) if root.is_dir() else [root]:
            _safe_parents(path)
            if path.is_dir():
                continue
            if not path.is_file():
                raise ResidentError(f"package input is not a regular file: {path}")
            relative = path.relative_to(app).as_posix()
            inputs[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return inputs


def _validate_tree(app: Path, files: set[str]) -> None:
    allowed_files = {*files, MANIFEST_NAME, *_GENERATED}
    allowed_directories = {
        str(parent)
        for name in allowed_files
        for parent in PurePosixPath(name).parents
        if str(parent) != "."
    }
    for path in app.rglob("*"):
        relative = path.relative_to(app).as_posix()
        if path.is_symlink():
            raise ResidentError("package tree contains a symlink")
        if path.is_dir():
            if relative not in allowed_directories:
                raise ResidentError("package tree contains an undeclared directory")
        elif not path.is_file() or relative not in allowed_files:
            raise ResidentError("package tree contains an undeclared or special file")


def _input_digest(app: Path, package: ResidentPackage) -> str:
    _safe_parents(app / MANIFEST_NAME)
    expected = {"package": asdict(package), "files": _source_hashes(app, package)}
    _validate_tree(app, set(expected["files"]))
    try:
        actual = (app / MANIFEST_NAME).read_bytes()
    except OSError as exc:
        raise ResidentError("source package manifest is missing") from exc
    if actual != _json_bytes(expected):
        raise ResidentError("source package manifest or committed input bytes changed")
    from planner.environments.resident.preflight import (
        CONFIGURATION_SOURCE,
        package_versions,
        versions_from_source,
    )

    if (app / CONFIGURATION_SOURCE).is_file() and versions_from_source(
        (app / CONFIGURATION_SOURCE).read_text()
    ) != package_versions(package):
        raise ResidentError("package compatibility differs from source configuration")
    return hashlib.sha256(actual).hexdigest()


def build_source_package(source: Path, package: ResidentPackage, destination: Path) -> Path:
    """Export standard package inputs from the exact commit, never the checkout."""
    validate_package(package)
    from planner.environments.resident.preflight import (
        CONFIGURATION_SOURCE,
        package_versions,
        requested_versions,
    )

    if any(
        CONFIGURATION_SOURCE == name or CONFIGURATION_SOURCE.startswith(name + "/")
        for name in package.closure.files
    ) and requested_versions(source, package.application_revision) != package_versions(package):
        raise ResidentError("package compatibility differs from exact source versions")
    _safe_parents(source)
    _safe_parents(destination)
    if destination.exists() or destination.is_symlink():
        raise ResidentError("candidate app path must be unused")
    try:
        kind = subprocess.check_output(
            ["git", "-C", str(source), "cat-file", "-t", package.application_revision],
            text=True,
        ).strip()
        if kind != "commit":
            raise ResidentError("source revision must name a commit object")
        result = subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "archive",
                "--format=tar",
                package.application_revision,
                "--",
                *package.closure.files,
            ],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ResidentError("exact source closure is unavailable at requested revision") from exc
    destination.mkdir(parents=True)
    try:
        found: set[str] = set()
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            for member in archive:
                name = member.name.rstrip("/")
                _relative(name)
                if member.isdir():
                    continue
                if (
                    not member.isfile()
                    or not any(
                        name == root or name.startswith(root + "/")
                        for root in package.closure.files
                    )
                    or name in found
                ):
                    raise ResidentError("source archive contains a non-regular or undeclared file")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ResidentError("source archive file is unavailable")
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(stream.read())
                found.add(name)
        if any(
            not any(name == root or name.startswith(root + "/") for name in found)
            for root in package.closure.files
        ):
            raise ResidentError("source closure contains missing files")
        (destination / MANIFEST_NAME).write_bytes(
            _json_bytes(
                {
                    "package": asdict(package),
                    "files": _source_hashes(destination, package),
                }
            )
        )
        _input_digest(destination, package)
    except BaseException:
        shutil.rmtree(destination)
        raise
    return destination


def _validate_native_runtime(prepared: PreparedResidentPackage) -> None:
    """Validate the permanent native runtime independently of app launchers."""
    validate_package(prepared.package)
    digest = _input_digest(prepared.app, prepared.package)
    _safe_parents(prepared.runtime)
    marker = prepared.runtime / RUNTIME_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ResidentError("native runtime has no immutable preparation marker")
    try:
        identity = installed_identity(prepared.runtime)
    except (ValueError, OSError) as exc:
        raise ResidentError(f"installed artifact validation failed: {exc}") from exc
    if marker.read_bytes() != _json_bytes(
        {
            "input_digest": digest,
            "runtime_path": str(prepared.runtime.absolute()),
            "installed_identity": identity,
        }
    ):
        raise ResidentError("native runtime input identity does not match source package")
    if prepared.runtime_digest and prepared.runtime_digest != digest:
        raise ResidentError("prepared runtime digest does not match package")
    python = prepared.runtime / "bin/python"
    # A native venv may use a symlink for its Python executable; its containing
    # directories and all runtime preparation inputs must remain regular paths.
    _safe_parents(python.parent)
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ResidentError("native runtime interpreter is absent or not executable")


def validate_runtime(prepared: PreparedResidentPackage) -> None:
    """Validate native input identity and the exact executable source payload."""
    _validate_native_runtime(prepared)
    for name, expected in (("bin/panels", _launcher_text(prepared)),):
        target = prepared.app / name
        _safe_parents(target)
        if not target.is_file() or target.read_text() != expected:
            raise ResidentError("generated executor entrypoint changed or is missing")
    if not os.access(prepared.app / "bin/panels", os.X_OK):
        raise ResidentError("generated executor launcher is not executable")


def prepare_runtime(
    app: Path,
    package: ResidentPackage,
    paths: ResidentPaths,
    builder: RuntimeBuilder,
) -> PreparedResidentPackage:
    """Build on the target Mac directly at the permanent runtime location.

    The supplied native builder must enforce platform/Python/dependency closure;
    no concrete builder is provided before the executor publishes that closure.
    A failed/incomplete runtime is not reused or renamed.
    """
    validate_package(package)
    digest = _input_digest(app, package)
    preflight = getattr(builder, "preflight", None)
    if preflight is not None:
        preflight(app, package)
    runtime = paths.runtimes / digest
    _safe_parents(runtime)
    if runtime.is_symlink():
        raise ResidentError("native runtime must not be a symlink")
    prepared = PreparedResidentPackage(package, app, runtime, digest)
    if runtime.exists():
        _validate_native_runtime(prepared)
        if not any((app / name).exists() for name in _GENERATED):
            _write_launcher(prepared)
        validate_runtime(prepared)
    else:
        runtime.parent.mkdir(parents=True, exist_ok=True)
        try:
            runtime.mkdir()  # exclusive creation: concurrent preparation must refuse
        except FileExistsError as exc:
            raise ResidentError("native runtime preparation already started") from exc
        try:
            builder(app, runtime, package)
            _write_launcher(prepared)
            (runtime / RUNTIME_MARKER).write_bytes(
                _json_bytes(
                    {
                        "input_digest": digest,
                        "runtime_path": str(prepared.runtime.absolute()),
                        "installed_identity": installed_identity(runtime),
                    }
                )
            )
            validate_runtime(prepared)
        except BaseException:
            # Keep incomplete state visible; never certify or reuse it on retry.
            (runtime / RUNTIME_MARKER).unlink(missing_ok=True)
            raise
    return prepared


def _launcher_text(prepared: PreparedResidentPackage) -> str:
    import shlex

    backend = ""
    if prepared.package.closure.node_package_roots:
        claude = prepared.runtime / (
            "node/agent_backends/node_modules/@anthropic-ai/claude-code/bin/claude.exe"
        )
        backend = (
            "export PLAN_CLAUDE_EXECUTABLE="
            + shlex.quote(str(claude))
            + "\n"
            + "export PATH="
            + shlex.quote(str(prepared.runtime / "bin"))
            + ':"$PATH"\n'
        )
    return (
        "#!/bin/sh\n"
        + backend
        + "exec "
        + shlex.quote(str(prepared.runtime / "bin/python"))
        + " -I -B -c "
        + shlex.quote(
            "from planner.executor.entrypoint import resident_main;resident_main("
            + repr(prepared.package.application_revision)
            + ")"
        )
        + ' "$@"\n'
    )


def _write_launcher(prepared: PreparedResidentPackage) -> None:
    launcher = prepared.app / "bin/panels"
    _safe_parents(launcher)
    launcher.parent.mkdir(exist_ok=True)
    launcher.write_text(_launcher_text(prepared))
    launcher.chmod(0o755)
