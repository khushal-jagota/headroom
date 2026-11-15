"""One action-based workspace/output check. No consent database or service access."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def access_description(path: Path, home: Path) -> str:
    for directory in ("Desktop", "Documents", "Downloads"):
        if path == home / directory or home / directory in path.parents:
            return f"{directory}: macOS can request Files and Folders consent."
    return "Ordinary account access; a redirected or protected path can require separate consent."


def check_access(workspace: Path, output: Path) -> tuple[Path, Path]:
    # Resolution is part of the explicit action: even path traversal can need consent.
    workspace = workspace.resolve(strict=True)
    output = output.resolve(strict=True)
    if not workspace.is_dir() or not output.is_dir():
        raise ValueError("workspace and output must be existing directories")
    # Read at most one directory entry, without opening arbitrary workspace files.
    with os.scandir(workspace) as entries:
        next(entries, None)
    with tempfile.NamedTemporaryFile(prefix="panels-access-", dir=output, mode="w+b") as probe:
        probe.write(b"Panels access check\n")
        probe.flush()
        probe.seek(0)
        if probe.read() != b"Panels access check\n":
            raise OSError("output read did not match the temporary write")
    return workspace, output
