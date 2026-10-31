"""Finite, fixed diagnostics at the existing connection retry boundary."""

import logging
import ssl
from collections.abc import Callable
from enum import StrEnum

import aiohttp


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
    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._seen: set[ConnectionFailureCategory] = set()

    def report(self, error: Exception, *, phase: ConnectionPhase) -> None:
        category = classify_connection_failure(error)
        if category not in self._seen:
            self._seen.add(category)
            self._emit(
                f"executor connection failure: {category.value} phase={phase.value}"
                f" exception={type(error).__name__}"
            )


# One finite budget for the process, including any replaced executor instance.
connection_diagnostics = ConnectionDiagnostics(logging.getLogger(__name__).warning)
