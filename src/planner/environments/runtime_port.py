"""Race-free launch-time port selection for on-demand environments."""

from __future__ import annotations

import errno
import socket
from collections.abc import Callable

from planner.core.config import HOST
from planner.environments.contracts import EnvironmentValidationError

SocketFactory = Callable[[], socket.socket]


def reserve_available_tcp_listener(
    *,
    bind_attempts: int,
    socket_factory: SocketFactory | None = None,
) -> tuple[socket.socket, int]:
    """Bind an OS-selected port and retain the listener through process exec."""
    factory = socket_factory or _tcp_socket
    last_error: OSError | None = None
    for _attempt in range(bind_attempts):
        listener = factory()
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((HOST, 0))
            listener.listen()
            listener.set_inheritable(True)
            return listener, int(listener.getsockname()[1])
        except OSError as exc:
            listener.close()
            if exc.errno != errno.EADDRINUSE:
                raise EnvironmentValidationError(
                    f"could not reserve a staging port: {exc}"
                ) from exc
            last_error = exc
    raise EnvironmentValidationError(
        f"could not reserve a staging port after {bind_attempts} attempts: {last_error}"
    )


def _tcp_socket() -> socket.socket:
    return socket.socket(socket.AF_INET, socket.SOCK_STREAM)
