"""Private durable records, independent of disposable Worker scratch."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


def read(path: Path) -> dict[str, Any]:
    if re.fullmatch(r"[0-9a-f]{32}\.json", path.name) is None:
        raise ValueError("Invalid Worker process record name")
    for candidate in (path.parent, path):
        info = candidate.lstat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or stat.S_ISLNK(info.st_mode):
            raise ValueError("Worker process record is not private and owned")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("Invalid Worker process record")
    if not isinstance(value.get("processes"), list) or not isinstance(value.get("ticket_id"), str):
        raise ValueError("Invalid Worker process ownership")
    return value


def write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def locked(path: Path, deadline: float) -> Iterator[None]:
    """Serialize every journal transaction across callers and process restarts."""
    lock_path = path.with_suffix(".lock")
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
            raise ValueError("Worker process lock is not private and owned")
        while True:
            if time.monotonic() >= deadline:
                raise TimeoutError("Worker process journal lock deadline expired")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(min(0.01, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
