"""Bounded polling for one externally written file.

Deployment state is written by the runner, outside SQLite and outside this
process.  This observer only bridges a filesystem change into Panels' existing
payload-free change signal; readers still re-open and validate the file.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

PathSignature = tuple[tuple[int, int, int, int] | None, tuple[int, int, int, int] | None]


def path_signature(path: Path) -> PathSignature:
    """Return a small parent/file signature without following the target file."""

    return (_stat_signature(path.parent), _stat_signature(path))


def _stat_signature(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat(follow_symlinks=False)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        # A probe failure is itself stable until the parent changes; readers will
        # report the evidence as unavailable.
        return (-1, -1, -1, -1)
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


async def observe_path_changes(
    path: Path,
    emit: Callable[[], None],
    *,
    poll_seconds: float = 0.25,
) -> None:
    """Emit after create, replacement, mutation, or deletion until cancelled."""

    if poll_seconds <= 0:
        raise ValueError("poll interval must be positive")
    previous = await asyncio.to_thread(path_signature, path)
    while True:
        await asyncio.sleep(poll_seconds)
        current = await asyncio.to_thread(path_signature, path)
        if current != previous:
            previous = current
            emit()
