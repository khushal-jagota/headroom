"""Fixed, rate-limited diagnostics at the existing connection retry boundary."""

import logging
import ssl
import time
from collections.abc import Callable
from enum import StrEnum

import aiohttp

from planner.executor.configuration import CONNECTION_FAILURE_LOG_INTERVAL_SECONDS


class ConnectionPhase(StrEnum):
    app_connect = "app_connect"
    hello = "hello"
    reconnect_ask = "reconnect_ask"
    steady_state = "steady_state"


class ConnectionFailureCategory(StrEnum):
    configuration = "configuration"
    tls = "tls"
    authentication = "authentication"
    timeout = "timeout"
    network = "network"
    connection_or_protocol = "connection_or_protocol"


def classify_connection_failure(error: Exception) -> ConnectionFailureCategory:
    if isinstance(error, aiohttp.InvalidURL):
        return ConnectionFailureCategory.configuration
    if isinstance(error, (aiohttp.ClientSSLError, ssl.SSLError)):
        return ConnectionFailureCategory.tls
    if isinstance(error, aiohttp.ClientResponseError) and error.status in (401, 403):
        return ConnectionFailureCategory.authentication
    if isinstance(error, TimeoutError):
        return ConnectionFailureCategory.timeout
    if isinstance(error, (OSError, aiohttp.ClientConnectionError)):
        return ConnectionFailureCategory.network
    return ConnectionFailureCategory.connection_or_protocol


class ConnectionDiagnostics:
    """Log every kind of failure when it first occurs, then repeats of that kind at most
    once per interval with how many occurred, so a reconnect loop cannot flood the log."""

    def __init__(
        self,
        emit: Callable[[str], None],
        *,
        interval_seconds: float = CONNECTION_FAILURE_LOG_INTERVAL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        # Kind -> (time of its last line, failures since then that no line reported).
        self._last: dict[
            tuple[ConnectionFailureCategory, ConnectionPhase, str], tuple[float, int]
        ] = {}

    def report(self, error: Exception, *, phase: ConnectionPhase) -> None:
        category = classify_connection_failure(error)
        kind = (category, phase, type(error).__name__)
        now = self._monotonic()
        last = self._last.get(kind)
        if last is not None and now - last[0] < self._interval_seconds:
            self._last[kind] = (last[0], last[1] + 1)
            return
        self._last[kind] = (now, 0)
        repeated = f" unlogged_repeats={last[1]}" if last is not None and last[1] else ""
        self._emit(
            f"executor connection failure: {category.value} phase={phase.value}"
            f" exception={type(error).__name__}{repeated}"
        )


def configure_executor_logging() -> None:
    """Give the Mac executor's log lines a UTC wall-clock time to the millisecond.

    Like ``logging.basicConfig``, this leaves an already configured root logger alone.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    formatter.converter = time.gmtime
    formatter.default_time_format = "%Y-%m-%dT%H:%M:%S"
    formatter.default_msec_format = "%s.%03dZ"
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)


# One rate limit for the process, including any replaced executor instance.
connection_diagnostics = ConnectionDiagnostics(logging.getLogger(__name__).warning)
