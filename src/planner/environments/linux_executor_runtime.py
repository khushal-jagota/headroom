"""Admit Linux executor archives through the existing Linux app archive checks."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from pydantic import TypeAdapter

from planner.environments.update_artifacts import extract_app_archive
from planner.executor.update_configuration import (
    MAC_RUNTIME_MANIFEST_NAME,
    MAX_MAC_RUNTIME_ARCHIVE_BYTES,
    MAX_MAC_RUNTIME_MANIFEST_BYTES,
)
from planner.platform_runtime.targets import LINUX_EXECUTOR, RuntimeTarget, require_runtime_target


def read_runtime_revision(runtime: Path, expected_revision: str | None = None) -> str:
    marker = runtime / MAC_RUNTIME_MANIFEST_NAME
    if marker.stat().st_size > MAX_MAC_RUNTIME_MANIFEST_BYTES:
        raise ValueError("Linux executor manifest exceeds its size bound")
    value = json.loads(marker.read_text())
    if not isinstance(value, dict):
        raise ValueError("Linux executor manifest must be an object")
    require_runtime_target(
        TypeAdapter(RuntimeTarget).validate_python(
            dict(
                system=value.get("platform"),
                architecture=value.get("architecture"),
                role=value.get("role"),
            )
        ),
        LINUX_EXECUTOR,
    )
    revision = value.get("application_revision")
    if (
        value.get("schema_version") != 1
        or not isinstance(revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        or (expected_revision is not None and revision != expected_revision)
    ):
        raise ValueError("Linux executor manifest differs from the approved release")
    python = runtime / ".venv/bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("Linux executor lacks its private executable interpreter")
    if (runtime / ".venv/pyvenv.cfg").exists():
        raise ValueError("Linux executor must not contain a relocated virtualenv")
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
        raise ValueError("Linux executor archive size or identity differs from approval")
    releases.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = releases / f"{expected_revision}-{expected_sha256}"
    if destination.exists():
        read_runtime_revision(destination, expected_revision)
        return destination
    temporary = Path(tempfile.mkdtemp(prefix=".install-", dir=releases))
    payload = temporary / "runtime"
    try:
        extract_app_archive(
            archive, payload, expected_sha256, expected_revision, expected_target=LINUX_EXECUTOR
        )
        read_runtime_revision(payload, expected_revision)
        payload.rename(destination)
        return destination
    finally:
        shutil.rmtree(temporary)
