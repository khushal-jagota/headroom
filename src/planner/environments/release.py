"""Host-native release export, manifest validation, and publication primitives."""

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

RELEASE_FORMAT = "panels-release-v1"
FULL_SHA_PATTERN = r"[0-9a-f]{40}"
HEX_DIGEST_PATTERN = r"[0-9a-f]{64}"


class ReleaseValidationError(ValueError):
    """Raised when a release cannot be trusted as a complete release."""


@dataclass(frozen=True)
class ReleaseManifest:
    release_sha: str
    source_digest: str
    artifact_digest: str

    def as_dict(self) -> dict[str, str]:
        return {
            "format": RELEASE_FORMAT,
            "release_sha": self.release_sha,
            "source_digest": self.source_digest,
            "artifact_digest": self.artifact_digest,
        }


def validate_release_sha(value: str, *, label: str = "release SHA") -> str:
    if fullmatch(FULL_SHA_PATTERN, value) is None:
        raise ReleaseValidationError(f"{label} must be a 40-character lowercase Git SHA")
    return value


def validate_release_manifest(
    path: Path,
    *,
    expected_sha: str | None = None,
    release_root: Path | None = None,
    require_runtime: bool = False,
) -> ReleaseManifest:
    manifest_input = path.expanduser()
    if manifest_input.is_symlink():
        raise ReleaseValidationError(f"release manifest must be a regular file: {manifest_input}")
    manifest_path = manifest_input.resolve()
    if not manifest_path.is_file():
        raise ReleaseValidationError(f"release manifest is missing: {manifest_path}")
    manifest_root = manifest_path.parent
    if (manifest_root / ".git").exists():
        raise ReleaseValidationError("release root must not contain Git metadata")
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError("release manifest is not valid JSON") from exc
    if not isinstance(value, dict) or value.get("format") != RELEASE_FORMAT:
        raise ReleaseValidationError("release manifest has an unsupported format")
    release_sha = value.get("release_sha")
    source_digest = value.get("source_digest")
    artifact_digest = value.get("artifact_digest")
    if not isinstance(release_sha, str):
        raise ReleaseValidationError("release manifest is missing release SHA")
    validate_release_sha(release_sha)
    if expected_sha is not None and release_sha != validate_release_sha(
        expected_sha, label="expected SHA"
    ):
        raise ReleaseValidationError("release manifest SHA does not match expected SHA")
    if not isinstance(source_digest, str) or fullmatch(HEX_DIGEST_PATTERN, source_digest) is None:
        raise ReleaseValidationError("release manifest has an invalid source digest")
    if (
        not isinstance(artifact_digest, str)
        or fullmatch(HEX_DIGEST_PATTERN, artifact_digest) is None
    ):
        raise ReleaseValidationError("release manifest has an invalid artifact digest")
    actual_digest = digest_release_artifact(manifest_root)
    if artifact_digest != actual_digest:
        raise ReleaseValidationError("release artifact digest does not match its manifest")
    if release_root is not None:
        expected_root = release_root.expanduser().resolve()
        try:
            relative = manifest_root.relative_to(expected_root)
        except ValueError as exc:
            raise ReleaseValidationError("release is outside the release root") from exc
        if len(relative.parts) != 1 or relative.name != release_sha:
            raise ReleaseValidationError("release directory must be the exact release SHA")
    _validate_release_tree(manifest_root)
    if require_runtime:
        _validate_runtime_tree(manifest_root)
    return ReleaseManifest(
        release_sha=release_sha, source_digest=source_digest, artifact_digest=artifact_digest
    )


def digest_release_source(release_root: Path) -> str:
    return _digest_tree(release_root, exclude_manifest=True)


def digest_release_artifact(release_root: Path) -> str:
    return _digest_tree(release_root, exclude_manifest=True)


def _digest_tree(release_root: Path, *, exclude_manifest: bool) -> str:
    digest = hashlib.sha256()
    root = release_root.resolve()
    for path in sorted(root.rglob("*")):
        if exclude_manifest and path.name == "manifest.json":
            continue
        if path.is_symlink():
            target = path.resolve()
            if root not in target.parents:
                raise ReleaseValidationError("release contains a symlink outside its root")
            relative = path.relative_to(root).as_posix().encode()
            digest.update(relative + b"\0SYMLINK\0" + os.readlink(path).encode() + b"\0")
            continue
        if not path.is_file() or ".git" in path.parts:
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative)
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _validate_release_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_symlink() and root not in path.resolve().parents:
            raise ReleaseValidationError("release contains a symlink outside its root")


def _validate_runtime_tree(root: Path) -> None:
    required_files = (root / ".venv" / "bin" / "python", root / "bin" / "panels-launcher")
    for path in required_files:
        if not path.is_file():
            raise ReleaseValidationError(f"runtime file is missing: {path.relative_to(root)}")
        if not os.access(path, os.X_OK):
            raise ReleaseValidationError(
                f"runtime file is not executable: {path.relative_to(root)}"
            )
    for path in (root / "web" / "dist", root / "agent_backends" / "node_modules"):
        if not path.is_dir():
            raise ReleaseValidationError(f"runtime directory is missing: {path.relative_to(root)}")


def build_exported_release(
    source_root: Path,
    *,
    requested_sha: str,
    release_root: Path,
    install_dependencies: bool = False,
) -> ReleaseManifest:
    source = source_root.expanduser().resolve()
    requested_sha = validate_release_sha(requested_sha, label="requested SHA")
    actual_sha = _git_output(source, "rev-parse", "HEAD")
    validate_release_sha(actual_sha, label="checkout HEAD")
    if actual_sha != requested_sha:
        raise ReleaseValidationError("checkout HEAD does not match requested SHA")
    destination = release_root.expanduser().resolve()
    if destination.exists():
        if destination.is_symlink():
            raise ReleaseValidationError("release root must not be a symlink")
        return validate_release_manifest(
            destination / "manifest.json",
            expected_sha=requested_sha,
            release_root=destination.parent,
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
        source_digest = digest_release_source(staging)
        if install_dependencies:
            _install_release_dependencies(staging)
        _write_stable_launcher(staging)
        artifact_digest = digest_release_artifact(staging)
        manifest = ReleaseManifest(requested_sha, source_digest, artifact_digest)
        (staging / "manifest.json").write_text(
            json.dumps(manifest.as_dict(), sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(staging, destination)
        destination.chmod(0o755)
        validate_release_manifest(
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
            ["git", "-C", str(source), *arguments], check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ReleaseValidationError("source checkout is not a usable Git checkout") from exc
    return result.stdout.strip()


def _install_release_dependencies(release_root: Path) -> None:
    subprocess.run(["python3", "-m", "venv", "--copies", str(release_root / ".venv")], check=True)
    python = release_root / ".venv" / "bin" / "python"
    requirements = release_root / "requirements.txt"
    if requirements.is_file():
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
    subprocess.run(
        [str(python), "-m", "pip", "install", "--editable", str(release_root)], check=True
    )
    for directory in (release_root / "web", release_root / "agent_backends"):
        if (directory / "package-lock.json").is_file():
            subprocess.run(["npm", "ci", "--prefix", str(directory)], check=True)
    web_package = release_root / "web" / "package.json"
    if web_package.is_file():
        subprocess.run(["npm", "run", "build", "--prefix", str(release_root / "web")], check=True)


def _write_stable_launcher(release_root: Path) -> None:
    launcher = release_root / "bin" / "panels-launcher"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        'root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)\n'
        'exec "$root/.venv/bin/python" -m planner.environments.release_launcher "$root" "$@"\n',
        encoding="utf-8",
    )
    launcher.chmod(0o755)
