"""Machine-wide advisory lock for the worker-step readiness poller.

The lock is acquired once per path and then held by the process (released only on
process exit or an explicit release). One cached OS fd per lock path."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from typing import Final

_LOCK_FDS: Final[dict[str, int]] = {}


def ensure_machine_lock(lock_path: str) -> bool:
    """Acquire (once) the machine-wide advisory lock; True when this process holds it."""
    if lock_path in _LOCK_FDS:
        return True
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return False
    _LOCK_FDS[lock_path] = fd
    return True


def release_machine_lock(lock_path: str) -> None:
    """Explicit release used by loop shutdown and tests. Safe no-op when not held."""
    fd = _LOCK_FDS.pop(lock_path, None)
    if fd is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
