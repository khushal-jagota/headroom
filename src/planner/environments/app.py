"""Host-native app export, manifest validation, and identity primitives."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from re import fullmatch

APP_FORMAT = "panels-app-v1"
FULL_SHA_PATTERN = r"[0-9a-f]{40}"
HEX_DIGEST_PATTERN = r"[0-9a-f]{64}"


class AppValidationError(ValueError):
    """Raised when an app cannot be trusted as a complete runtime."""


@dataclass(frozen=True)
class AppManifest:
    app_sha: str
    source_digest: str
    artifact_digest: str

    def as_dict(self) -> dict[str, str]:
        return {
            "format": APP_FORMAT,
            "app_sha": self.app_sha,
            "source_digest": self.source_digest,
            "artifact_digest": self.artifact_digest,
        }


def validate_app_sha(value: str, *, label: str = "app SHA") -> str:
    if fullmatch(FULL_SHA_PATTERN, value) is None:
        raise AppValidationError(f"{label} must be a 40-character lowercase Git SHA")
    return value


def validate_app_manifest(
    path: Path,
    *,
    expected_sha: str | None = None,
    require_runtime: bool = False,
) -> AppManifest:
    manifest_input = path.expanduser()
    if manifest_input.is_symlink():
        raise AppValidationError(f"app manifest must be a regular file: {manifest_input}")
    manifest_path = manifest_input.resolve()
    if not manifest_path.is_file():
        raise AppValidationError(f"app manifest is missing: {manifest_path}")
    app_root = manifest_path.parent
    if (app_root / ".git").exists():
        raise AppValidationError("app root must not contain Git metadata")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AppValidationError("app manifest is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("format") != APP_FORMAT:
        raise AppValidationError("app manifest has an unsupported format")
    app_sha = value.get("app_sha")
    source_digest = value.get("source_digest")
    artifact_digest = value.get("artifact_digest")
    if not isinstance(app_sha, str):
        raise AppValidationError("app manifest is missing app SHA")
    validate_app_sha(app_sha)
    if expected_sha is not None and app_sha != validate_app_sha(expected_sha, label="expected SHA"):
        raise AppValidationError("app manifest SHA does not match expected SHA")
    if not isinstance(source_digest, str) or fullmatch(HEX_DIGEST_PATTERN, source_digest) is None:
        raise AppValidationError("app manifest has an invalid source digest")
    if not isinstance(artifact_digest, str) or fullmatch(
        HEX_DIGEST_PATTERN, artifact_digest
    ) is None:
        raise AppValidationError("app manifest has an invalid artifact digest")
    artifact_matches = artifact_digest == digest_app_artifact(app_root)
    if not artifact_matches:
        # Legacy v1 manifests included build-time caches. Ignore only caches
        # created after that manifest so existing deployments remain verifiable.
        artifact_matches = artifact_digest == _digest_tree(
            app_root,
            include_python_cache_before_ns=manifest_path.stat().st_mtime_ns,
        )
    if not artifact_matches:
        raise AppValidationError("app artifact digest does not match its manifest")
    _validate_app_tree(app_root)
    if require_runtime:
        _validate_runtime_tree(app_root)
    return AppManifest(app_sha, source_digest, artifact_digest)


def digest_app_source(app_root: Path) -> str:
    return _digest_tree(app_root)


def digest_app_artifact(app_root: Path) -> str:
    return _digest_tree(app_root)


def _digest_tree(
    app_root: Path, *, include_python_cache_before_ns: int | None = None
) -> str:
    digest = hashlib.sha256()
    root = app_root.resolve()
    for path in sorted(root.rglob("*")):
        if path == root / "manifest.json":
            continue
        relative_path = path.relative_to(root)
        is_python_cache = "__pycache__" in relative_path.parts or path.suffix in {
            ".pyc",
            ".pyo",
        }
        if is_python_cache:
            if include_python_cache_before_ns is None:
                continue
            try:
                if path.lstat().st_mtime_ns > include_python_cache_before_ns:
                    continue
            except FileNotFoundError:
                continue
        if path.is_symlink():
            target = path.resolve()
            if root not in target.parents:
                raise AppValidationError("app contains a symlink outside its root")
            relative = path.relative_to(root).as_posix().encode()
            digest.update(relative + b"\0SYMLINK\0" + os.readlink(path).encode() + b"\0")
            continue
        if not path.is_file() or ".git" in path.parts:
            continue
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_app_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if ".git" in path.relative_to(root).parts:
            raise AppValidationError("app must not contain Git metadata")
        if path.is_symlink() and root not in path.resolve().parents:
            raise AppValidationError("app contains a symlink outside its root")


def _validate_runtime_tree(root: Path) -> None:
    python = root / ".venv" / "bin" / "python"
    required_files = (
        python,
        root / "bin" / "panels",
        root / "bin" / "panels-launcher",
    )
    for path in required_files:
        if not path.is_file():
            raise AppValidationError(f"runtime file is missing: {path.relative_to(root)}")
        if not os.access(path, os.X_OK):
            raise AppValidationError(f"runtime file is not executable: {path.relative_to(root)}")
    for path in (
        root / "web" / "dist" / "index.html",
        root / "agent_backends" / "node_modules" / ".package-lock.json",
    ):
        if not path.is_file():
            raise AppValidationError(f"runtime file is missing: {path.relative_to(root)}")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    try:
        imported = subprocess.run(
            [
                str(python),
                "-c",
                "import pathlib, planner; print(pathlib.Path(planner.__file__).resolve())",
            ],
            check=True,
            capture_output=True,
            text=True,
            cwd=root,
            env=environment,
        ).stdout.strip()
        imported_path = Path(imported).resolve()
        imported_path.relative_to(root.resolve())
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise AppValidationError("app interpreter does not import planner from its app") from exc
    if not imported_path.is_file():
        raise AppValidationError("app interpreter resolved a missing planner package")


def build_exported_app(
    source_root: Path,
    *,
    requested_sha: str,
    candidate_app: Path,
    install_dependencies: bool = False,
) -> AppManifest:
    source = source_root.expanduser().resolve()
    requested_sha = validate_app_sha(requested_sha, label="requested SHA")
    actual_sha = _git_output(source, "rev-parse", "HEAD")
    validate_app_sha(actual_sha, label="checkout HEAD")
    if actual_sha != requested_sha:
        raise AppValidationError("checkout HEAD does not match requested SHA")
    destination = candidate_app.expanduser().resolve()
    if destination.exists():
        if destination.is_symlink():
            raise AppValidationError("candidate app must not be a symlink")
        return validate_app_manifest(
            destination / "manifest.json",
            expected_sha=requested_sha,
            require_runtime=install_dependencies,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}-", dir=str(destination.parent)))
    try:
        archive = subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", requested_sha],
            check=True,
            capture_output=True,
        )
        with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as tar:
            tar.extractall(staging, filter="data")
        source_digest = digest_app_source(staging)
        if install_dependencies:
            _install_app_dependencies(staging)
        _write_app_entrypoints(staging, app_sha=requested_sha)
        manifest = AppManifest(requested_sha, source_digest, digest_app_artifact(staging))
        (staging / "manifest.json").write_text(
            json.dumps(manifest.as_dict(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
        destination.chmod(0o755)
        validate_app_manifest(
            destination / "manifest.json",
            expected_sha=requested_sha,
            require_runtime=install_dependencies,
        )
        return manifest
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _git_output(source: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AppValidationError("source checkout is not a usable Git checkout") from exc
    return result.stdout.strip()


def _install_app_dependencies(app_root: Path) -> None:
    subprocess.run(["python3", "-m", "venv", "--copies", str(app_root / ".venv")], check=True)
    python = app_root / ".venv" / "bin" / "python"
    requirements = app_root / "requirements.txt"
    if requirements.is_file():
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
    subprocess.run([str(python), "-m", "pip", "install", str(app_root)], check=True)
    for directory in (app_root / "web", app_root / "agent_backends"):
        if (directory / "package-lock.json").is_file():
            subprocess.run(["npm", "ci", "--prefix", str(directory)], check=True)
    if (app_root / "web" / "package.json").is_file():
        subprocess.run(["npm", "run", "build", "--prefix", str(app_root / "web")], check=True)


def _write_app_entrypoints(app_root: Path, *, app_sha: str) -> None:
    app_sha = validate_app_sha(app_sha)
    bin_directory = app_root / "bin"
    bin_directory.mkdir(parents=True, exist_ok=True)
    cli = bin_directory / "panels"
    cli.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P)\n'
        f'export PLAN_APP_ROOT="$root" PLAN_APP_SHA="{app_sha}"\n'
        'exec "$root/.venv/bin/python" -I -m planner "$@"\n',
        encoding="utf-8",
    )
    cli.chmod(0o755)
    launcher = bin_directory / "panels-launcher"
    launcher.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
        'exec "$root/.venv/bin/python" -m planner.environments.app_launcher "$root" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
