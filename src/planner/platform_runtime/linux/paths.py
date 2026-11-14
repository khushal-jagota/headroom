"""Linux installation paths and advisory lock primitives."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Mapping
from pathlib import Path

_CONTROL_SOCKET_ENV = "PLAN_SERVER_CONTROL_SOCKET"


def _server_lifecycle_directory() -> Path:
    directory = Path("/tmp") / f"panels-{os.getuid()}"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    return directory


def resolve_server_control_socket_path(
    port: int,
    environ: Mapping[str, str],
    launch_root: Path,
) -> Path:
    explicit = environ.get(_CONTROL_SOCKET_ENV, "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            path = launch_root / path
        return path.resolve(strict=False)
    return _server_lifecycle_directory() / f"server-{port}.sock"


def resolve_event_loop_tasks_socket_path(control_socket_path: Path) -> Path:
    """Name the application's own socket for its event loop tasks, beside the control socket.

    The control socket belongs to the supervisor, which has no event loop and must keep
    answering restart even when the application is stuck. The event loop tasks are answered by
    the application process itself, on its event loop, through this sibling socket.
    """
    return control_socket_path.with_name(
        f"{control_socket_path.stem}-event-loop-tasks{control_socket_path.suffix}"
    )


def resolve_server_lifecycle_lease_path(port: int) -> Path:
    return _server_lifecycle_directory() / f"server-{port}.lock"




def lock_exclusive(descriptor: int, *, nonblocking: bool = False) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))


def unlock(descriptor: int) -> None:
    fcntl.flock(descriptor, fcntl.LOCK_UN)
