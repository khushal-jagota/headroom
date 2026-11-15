"""Verify a prebuilt native runtime before it becomes an executable selection.

The archive contains its interpreter. Installation never relocates a virtualenv,
executes a build step, replaces the fixed launcher, or touches executor data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from pydantic import TypeAdapter

from planner.executor.update_configuration import (
    MAC_RUNTIME_MANIFEST_NAME,
    MAX_MAC_RUNTIME_ARCHIVE_BYTES,
    MAX_MAC_RUNTIME_EXPANDED_BYTES,
    MAX_MAC_RUNTIME_MANIFEST_BYTES,
)
from planner.executor.update_contracts import MacRuntimeManifest
from planner.platform_runtime.targets import MAC_EXECUTOR, RuntimeTarget, require_runtime_target


def read_runtime_revision(runtime: Path, expected_revision: str | None = None) -> str:
    if (runtime / MAC_RUNTIME_MANIFEST_NAME).stat().st_size > MAX_MAC_RUNTIME_MANIFEST_BYTES:
        raise ValueError("Mac runtime manifest exceeds its size bound")
    manifest = TypeAdapter(MacRuntimeManifest).validate_python(
        json.loads((runtime / MAC_RUNTIME_MANIFEST_NAME).read_text())
    )
    try:
        require_runtime_target(
            RuntimeTarget(manifest.platform, manifest.architecture, "executor"), MAC_EXECUTOR
        )
    except ValueError as exc:
        raise ValueError("Mac runtime manifest differs from the approved release") from exc
    revision = manifest.application_revision
    if (
        manifest.schema_version != 1
        or not isinstance(revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        or (expected_revision is not None and revision != expected_revision)
    ):
        raise ValueError("Mac runtime manifest differs from the approved release")
    python = runtime / "python/bin/python3"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Mac runtime lacks its prebuilt executable interpreter")
    # A copied virtualenv still names its builder's base interpreter.
    if (runtime / "python/pyvenv.cfg").exists():
        raise ValueError("Mac runtime must not contain a relocated virtualenv")
    return revision


def extract_runtime(
    archive: Path,
    releases: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    expected_revision: str,
) -> Path:
    if (
        re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or re.fullmatch(r"[0-9a-f]{40}", expected_revision) is None
        or not 0 < expected_size <= MAX_MAC_RUNTIME_ARCHIVE_BYTES
        or archive.stat().st_size != expected_size
    ):
        raise ValueError("Mac runtime archive size or identity differs from approval")
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Mac runtime archive digest differs from approval")
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = releases / f"{expected_revision}-{expected_sha256}"
    if destination.exists():
        read_runtime_revision(destination, expected_revision)
        return destination
    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=releases))
    try:
        try:
            with tarfile.open(archive, "r:gz") as packed:
                names: set[str] = set()
                expanded = 0
                members = packed.getmembers()
                for member in members:
                    path = PurePosixPath(member.name)
                    if (
                        path.is_absolute()
                        or not path.parts
                        or ".." in path.parts
                        or "\\" in member.name
                        or str(path) in names
                        or not (member.isfile() or member.isdir() or member.issym())
                    ):
                        raise ValueError("Mac runtime archive contains an unsafe member")
                    names.add(str(path))
                    expanded += member.size
                    if expanded > MAX_MAC_RUNTIME_EXPANDED_BYTES:
                        raise ValueError("Mac runtime archive exceeds its expanded size bound")
                # Python's data filter checks link targets and resolved parents at each
                # extraction, including a symlink introduced by an earlier member.
                packed.extractall(temporary, members=members, filter="data")
        except (tarfile.TarError, EOFError) as error:
            raise ValueError("Mac runtime archive is invalid") from error
        read_runtime_revision(temporary, expected_revision)
        temporary.rename(destination)
        return destination
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
