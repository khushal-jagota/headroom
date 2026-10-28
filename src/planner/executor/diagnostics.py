"""Finite, fixed diagnostics at the existing connection retry boundary."""

import asyncio
import logging
import ssl
from collections.abc import Callable

import aiohttp
from pydantic import ValidationError

from planner.executor.contracts import (
    ConnectionFailureCategory,
    ConnectionFailureOrigin,
    ConnectionPhase,
)
from planner.executor.wire import ProtocolRefused


def connection_exception_tag(error: Exception) -> str:
    # Exact types only: custom subclass names and messages are never diagnostics.
    return {
        aiohttp.InvalidURL: "InvalidURL",
        aiohttp.ClientConnectorCertificateError: "ClientConnectorCertificateError",
        aiohttp.ClientConnectorSSLError: "ClientConnectorSSLError",
        aiohttp.WSServerHandshakeError: "WSServerHandshakeError",
        aiohttp.ClientConnectionError: "ClientConnectionError",
        aiohttp.ClientPayloadError: "ClientPayloadError",
        aiohttp.ServerDisconnectedError: "ServerDisconnectedError",
        ssl.SSLCertVerificationError: "SSLCertVerificationError",
        ssl.SSLError: "SSLError",
        asyncio.IncompleteReadError: "IncompleteReadError",
        ProtocolRefused: "ProtocolRefused",
        ValidationError: "ValidationError",
        ValueError: "ValueError",
        KeyError: "KeyError",
        TypeError: "TypeError",
        OSError: "OSError",
        ConnectionError: "ConnectionError",
        ConnectionAbortedError: "ConnectionAbortedError",
        ConnectionRefusedError: "ConnectionRefusedError",
        ConnectionResetError: "ConnectionResetError",
        BrokenPipeError: "BrokenPipeError",
        TimeoutError: "TimeoutError",
    }.get(type(error), "other")


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
        self._context_seen = False

    def report(
        self,
        error: Exception,
        *,
        phase: ConnectionPhase | None = None,
        origin: ConnectionFailureOrigin | None = None,
    ) -> None:
        category = classify_connection_failure(error)
        contextual = phase is not None and origin is not None and not self._context_seen
        if category not in self._seen or contextual:
            self._seen.add(category)
            line = f"executor connection failure: {category.value}"
            if contextual and phase is not None and origin is not None:
                self._context_seen = True
                line += (
                    f" phase={phase.value} origin={origin.value}"
                    f" exception={connection_exception_tag(error)}"
                )
            self._emit(line)


# One finite budget for the process, including any replaced executor instance.
connection_diagnostics = ConnectionDiagnostics(logging.getLogger(__name__).warning)
