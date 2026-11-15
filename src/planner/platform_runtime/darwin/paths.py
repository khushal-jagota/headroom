"""Private Darwin runtime addresses stay below the Unix-socket byte limit."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path

from planner.server_lifecycle.contracts import ServerLifecycleError

_RUNTIME_PARENT = Path("/var/tmp")
_SOCKET_LIMIT = 104


def _runtime_directory() -> Path:
    directory = _RUNTIME_PARENT / f"panels-{os.getuid()}"
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        pass
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ServerLifecycleError(f"Panels runtime folder must be private and owned: {directory}")
    return directory


def _socket_path(path: Path) -> Path:
    if len(os.fsencode(path)) < _SOCKET_LIMIT:
        return path
    digest = hashlib.sha256(os.fsencode(path)).hexdigest()[:24]
    short = _runtime_directory() / f"s-{digest}.sock"
    if len(os.fsencode(short)) >= _SOCKET_LIMIT:
        raise ServerLifecycleError("Panels runtime folder is too long for macOS Unix sockets")
    return short


def resolve_server_control_socket_path(
    port: int, environ: Mapping[str, str], launch_root: Path
) -> Path:
    explicit = environ.get("PLAN_SERVER_CONTROL_SOCKET", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = launch_root / path
        return _socket_path(path.absolute())
    return _socket_path(_runtime_directory() / f"server-{port}.sock")


def resolve_event_loop_tasks_socket_path(control_socket_path: Path) -> Path:
    return _socket_path(
        control_socket_path.with_name(
            f"{control_socket_path.stem}-event-loop-tasks{control_socket_path.suffix}"
        )
    )


def resolve_server_lifecycle_lease_path(port: int) -> Path:
    return _runtime_directory() / f"server-{port}.lock"
