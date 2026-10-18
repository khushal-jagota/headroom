"""Read-only, standard-library inspection before any deployment host mutation."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from planner.environments.resident.contracts import (
    ExecutorCompatibilityVersions,
    InstalledExecutorCompatibility,
    ResidentError,
    ResidentPackage,
)
from planner.environments.resident.installed import installed_identity
from planner.environments.resident.logic.upgrade import require_supported_upgrade
from planner.executor.configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION

CONFIGURATION_SOURCE = "src/planner/executor/configuration.py"


def _literal_versions(source: str) -> dict[str, int]:
    names = {"PROTOCOL_VERSION": "protocol_version", "SPOOL_FORMAT_VERSION": "spool_format_version"}
    result: dict[str, int] = {}
    for node in ast.parse(source).body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else ([node.target] if isinstance(node, ast.AnnAssign) else [])
        )
        for target in targets:
            if isinstance(target, ast.Name) and target.id in names:
                key = names[target.id]
                value = node.value  # type: ignore[attr-defined]
                if (
                    key in result
                    or not isinstance(value, ast.Constant)
                    or type(value.value) is not int
                    or value.value <= 0
                ):
                    raise ValueError("executor versions must be unique positive integer literals")
                result[key] = value.value
    if set(result) != set(names.values()):
        raise ValueError("executor compatibility versions are missing")
    tree = ast.parse(source)
    for name in names:
        writes = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
        ]
        if len(writes) != 1 or any(
            (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name == name
            )
            or (isinstance(node, ast.alias) and (node.asname or node.name) == name)
            for node in ast.walk(tree)
        ):
            raise ValueError("executor compatibility versions contain unsupported reassignment")
    return result


def _path_present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        # An absent leaf through a dangling parent is not confirmed absence.
        for parent in path.parents:
            if parent.is_symlink() and not parent.exists():
                raise ValueError("dangling managed installation path") from None
        return False
    return True


def _regular_read(path: Path) -> bytes:
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("symlink in resident installation evidence")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
        raise ValueError("resident installation evidence is not an owner regular file")
    return path.read_bytes()


def _mac_identity(home: Path) -> dict[str, object]:
    root = home / "Deployments/PanelsExecutor"
    app = root / "current/app"
    if not _path_present(app):
        evidence = (
            root / "initial-install.json",
            home / "Library/Application Support/Panels/executor",
            home / "Library/LaunchAgents/com.panels.executor.plist",
        )
        if any(_path_present(path) for path in evidence):
            raise ValueError("absent Mac app conflicts with existing installation evidence")
        return {"host": "mac", "application_revision": None, "versions": None}
    manifest_bytes = _regular_read(app / "resident-package.json")
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict):
        raise ValueError("resident manifest is not an object")
    package, files = manifest.get("package"), manifest.get("files")
    if not isinstance(package, dict) or not isinstance(files, dict):
        raise ValueError("resident manifest lacks package/source identity")
    revision = package.get("application_revision")
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("invalid installed resident revision")
    versions = {key: package.get(key) for key in ("protocol_version", "spool_format_version")}
    if any(type(value) is not int or value <= 0 for value in versions.values()):
        raise ValueError("invalid installed resident versions")
    name = "src/planner/executor/configuration.py"
    source = _regular_read(app / name)
    if files.get(name) != hashlib.sha256(source).hexdigest():
        raise ValueError("installed executor configuration differs from resident manifest")
    if _literal_versions(source.decode()) != versions:
        raise ValueError("installed executor source and manifest versions disagree")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    runtime = root / "runtimes" / digest
    marker = json.loads(_regular_read(runtime / "resident-runtime.json"))
    if marker != {
        "input_digest": digest,
        "runtime_path": str(runtime.absolute()),
        "installed_identity": installed_identity(runtime),
    }:
        raise ValueError("installed runtime and source identity disagree")
    return {"host": "mac", "application_revision": revision, "versions": versions}


def versions_from_source(source: str) -> ExecutorCompatibilityVersions:
    try:
        return ExecutorCompatibilityVersions(**_literal_versions(source))
    except (ValueError, SyntaxError) as exc:
        raise ResidentError(f"Invalid executor configuration: {exc}") from exc


def supported_versions() -> ExecutorCompatibilityVersions:
    return ExecutorCompatibilityVersions(PROTOCOL_VERSION, SPOOL_FORMAT_VERSION)


def package_versions(package: ResidentPackage) -> ExecutorCompatibilityVersions:
    return ExecutorCompatibilityVersions(package.protocol_version, package.spool_format_version)


def requested_versions(source: Path, revision: str) -> ExecutorCompatibilityVersions:
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ResidentError("requested revision must be a full lowercase SHA")
    try:
        result = subprocess.run(
            ["git", "-C", str(source), "show", f"{revision}:{CONFIGURATION_SOURCE}"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ResidentError("Exact requested executor configuration is unavailable") from exc
    return versions_from_source(result.stdout)


def parse_mac_identity(output: bytes) -> InstalledExecutorCompatibility:
    try:
        if len(output) > 4096:
            raise ValueError("oversized inspection response")
        value = json.loads(output)
        if (
            not isinstance(value, dict)
            or set(value) != {"host", "application_revision", "versions"}
            or value["host"] != "mac"
        ):
            raise ValueError("invalid inspection fields")
        revision, versions = value["application_revision"], value["versions"]
        if revision is None and versions is None:
            return InstalledExecutorCompatibility("mac", None, None)
        if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise ValueError("invalid inspection revision")
        if (
            not isinstance(versions, dict)
            or set(versions) != {"protocol_version", "spool_format_version"}
            or any(type(item) is not int or item <= 0 for item in versions.values())
        ):
            raise ValueError("invalid inspection versions")
        return InstalledExecutorCompatibility(
            "mac", revision, ExecutorCompatibilityVersions(**versions)
        )
    except (ValueError, TypeError) as exc:
        raise ResidentError(f"Mac installed identity is unknown: {exc}") from exc


def inspect_installed_mac(home: Path) -> InstalledExecutorCompatibility:
    try:
        return parse_mac_identity(json.dumps(_mac_identity(home)).encode())
    except (OSError, ValueError, SyntaxError) as exc:
        raise ResidentError(f"Mac installed identity is unknown: {exc}") from exc


def inspect_installed_vps(app: Path) -> InstalledExecutorCompatibility:
    from planner.environments.app import validate_app_manifest

    try:
        if not _path_present(app):
            if any(
                _path_present(path) for path in (app.parent / "data", app.parent / "manifest.json")
            ):
                raise ValueError("absent VPS app conflicts with existing installation evidence")
            return InstalledExecutorCompatibility("vps", None, None)
        manifest = validate_app_manifest(app / "manifest.json")
        versions = versions_from_source((app / CONFIGURATION_SOURCE).read_text())
        return InstalledExecutorCompatibility("vps", manifest.app_sha, versions)
    except (OSError, ValueError) as exc:
        raise ResidentError(f"VPS installed identity is unknown: {exc}") from exc


def require_mac_upgrade(home: Path, package: ResidentPackage) -> None:
    require_supported_upgrade(
        package_versions(package), (inspect_installed_mac(home),), supported_versions()
    )


def mac_probe_source() -> str:
    """Deliver the exact local read-only inspector without importing remote code."""
    functions = (installed_identity, _literal_versions, _path_present, _regular_read, _mac_identity)
    return (
        "from __future__ import annotations\n"
        "import ast,hashlib,json,os,re,stat\nfrom pathlib import Path, PurePosixPath\n"
        + "\n".join(inspect.getsource(function) for function in functions)
    )


def mac_probe_command_source() -> str:
    return mac_probe_source() + "\nprint(json.dumps(_mac_identity(Path.home().absolute())))\n"


def mac_admission_source(versions: ExecutorCompatibilityVersions) -> str:
    """Bootstrap guard runs before staging mkdir, extraction or native preparation."""
    expected = {
        "protocol_version": versions.protocol_version,
        "spool_format_version": versions.spool_format_version,
    }
    return (
        mac_probe_source()
        + "\ninstalled=_mac_identity(Path.home().absolute())\n"
        + f"if installed['versions'] is not None and installed['versions'] != {expected!r}:\n"
        + " raise ValueError('Mac installed executor protocol/spool transition refused')\n"
    )
