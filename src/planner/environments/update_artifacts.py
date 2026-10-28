"""Immutable executable archives shared by publication and adoption.

GitHub's outer artifact ZIP does not preserve Unix modes. The inner tar does.
No extraction is admitted until the complete archive hash matches its receipt.
"""
from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from pathlib import Path

from planner.environments.app import AppManifest, validate_app_manifest

APP_ARCHIVE_NAME = "panels-app.tar.gz"
PUBLICATION_RECEIPT_NAME = "publication.json"


def hash_archive(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_app_archive(app: Path, archive: Path) -> str:
    """Seal the built tree before any smoke execution can change it."""
    validate_app_manifest(app / "manifest.json", require_runtime=True)
    if archive.resolve().is_relative_to(app.resolve()):
        raise ValueError("archive must be outside the candidate app")
    # Exclusive creation prevents accidentally replacing an already proved archive.
    with archive.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as bundle:
        for path in sorted(app.rglob("*")):
            if path.is_symlink() and os.path.isabs(os.readlink(path)):
                raise ValueError("app archive requires relative symlinks")
            if stat.S_IMODE(path.lstat().st_mode) & ~0o777:
                raise ValueError("app archive contains special permission bits")
            bundle.add(path, arcname=path.relative_to(app).as_posix(), recursive=False)
    return hash_archive(archive)


def extract_app_archive(
    archive: Path, destination: Path, archive_sha256: str, expected_sha: str
) -> AppManifest:
    """Validate safe paths, exact bytes, runtime identity, and preserved modes."""
    if hash_archive(archive) != archive_sha256:
        raise ValueError("app archive hash differs from publication receipt")
    if destination.exists():
        raise ValueError("archive destination must not exist")
    destination.mkdir(parents=True)
    with tarfile.open(archive, mode="r:gz") as bundle:
        members = bundle.getmembers()
        names: set[str] = set()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or member.name in names:
                raise ValueError("app archive contains unsafe or duplicate paths")
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                raise ValueError("app archive contains unsupported entries")
            if member.mode & ~0o777:
                raise ValueError("app archive contains special permission bits")
            names.add(member.name)

        def safe_member(member: tarfile.TarInfo, root: str) -> tarfile.TarInfo | None:
            checked = tarfile.data_filter(member, root)
            if checked is not None:
                # data_filter guards links and paths. Preserve ordinary Unix modes.
                checked.mode = member.mode
            return checked

        bundle.extractall(destination, members=members, filter=safe_member)
        for member in members:
            path = destination / member.name
            if not member.issym() and stat.S_IMODE(path.stat().st_mode) != member.mode:
                raise ValueError("app archive did not preserve permissions")
    return validate_app_manifest(
        destination / "manifest.json", expected_sha=expected_sha, require_runtime=True
    )
