"""Linux evidence associates observed processes with a worktree, not authorship.

Discovery requires exactly one readable same-UID listener owner. Linux may hide
non-dumpable processes, so this cannot exclude additional hidden socket holders.
The pinned process and its accepted reverse TCP tuple must always be readable.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from planner.platform_runtime.linux.preview import LinuxProcessInspector as LinuxProcessInspector
from planner.previews import config
from planner.previews.contracts import PreviewError


def validate_worktree(project_folder: Path, ticket_id: str) -> Path:
    """Require the exact reserved directory, with no symlink components or bare repo."""
    deadline = time.monotonic() + config.INSPECTION_SECONDS
    folder = project_folder.absolute()
    candidate = folder / "worktrees" / f"{ticket_id}-preview"
    try:
        if folder.resolve(strict=True) != folder or candidate.resolve(strict=True) != candidate:
            raise PreviewError("Preview worktree path must not contain symlink components.")
        result = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=max(0.001, deadline - time.monotonic()),
            check=False,
        )
        if result.returncode or Path(result.stdout.strip()) != candidate:
            raise PreviewError("The reserved path must be a Git worktree root.")
        listed = subprocess.run(
            ["git", "-C", str(candidate), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=max(0.001, deadline - time.monotonic()),
            check=False,
        )
        if listed.returncode or f"worktree {candidate}\n" not in listed.stdout:
            raise PreviewError("The reserved directory is not a registered Git worktree.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreviewError("Cannot validate the Ticket preview Git worktree.") from exc
    return candidate
