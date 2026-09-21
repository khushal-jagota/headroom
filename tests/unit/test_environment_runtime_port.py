from __future__ import annotations

import errno
import socket

import pytest

from planner.environments.contracts import EnvironmentValidationError
from planner.environments.runtime_port import reserve_available_tcp_listener


class _FakeSocket:
    def __init__(self, *, bind_error: OSError | None = None, port: int = 43210) -> None:
        self.bind_error = bind_error
        self.port = port
        self.closed = False
        self.inheritable = False

    def setsockopt(self, *_args: object) -> None:
        return

    def bind(self, address: tuple[str, int]) -> None:
        assert address == ("127.0.0.1", 0)
        if self.bind_error is not None:
            raise self.bind_error

    def listen(self) -> None:
        return

    def set_inheritable(self, inheritable: bool) -> None:
        self.inheritable = inheritable

    def getsockname(self) -> tuple[str, int]:
        return "127.0.0.1", self.port

    def close(self) -> None:
        self.closed = True


def test_dynamic_port_stops_after_bounded_conflicts() -> None:
    made: list[_FakeSocket] = []

    def factory() -> _FakeSocket:
        candidate = _FakeSocket(bind_error=OSError(errno.EADDRINUSE, "taken"))
        made.append(candidate)
        return candidate

    with pytest.raises(EnvironmentValidationError, match="after 2 attempts"):
        reserve_available_tcp_listener(
            bind_attempts=2,
            socket_factory=factory,  # type: ignore[arg-type]
        )
    assert len(made) == 2
    assert all(candidate.closed for candidate in made)


def test_real_dynamic_listener_owns_the_reported_port_until_closed() -> None:
    listener, port = reserve_available_tcp_listener(bind_attempts=1)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as competitor:
            with pytest.raises(OSError) as caught:
                competitor.bind(("127.0.0.1", port))
        assert caught.value.errno == errno.EADDRINUSE
    finally:
        listener.close()
