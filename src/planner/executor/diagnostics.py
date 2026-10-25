"""Finite, fixed diagnostics at the existing connection retry boundary."""

import asyncio
import logging
from collections.abc import Callable

import asyncssh
from pydantic import ValidationError

from planner.executor.contracts import (
    ConnectionFailureCategory,
    ConnectionFailureOrigin,
    ConnectionPhase,
    SshConfigurationError,
    TailnetValidationError,
)
from planner.executor.wire import ProtocolRefused


def connection_exception_tag(error: Exception) -> str:
    # Exact types only: custom subclass names and messages are never diagnostics.
    return {
        SshConfigurationError: "SshConfigurationError",
        TailnetValidationError: "TailnetValidationError",
        asyncssh.HostKeyNotVerifiable: "HostKeyNotVerifiable",
        asyncssh.PermissionDenied: "PermissionDenied",
        asyncssh.ChannelOpenError: "ChannelOpenError",
        asyncssh.ConnectionLost: "ConnectionLost",
        asyncssh.DisconnectError: "DisconnectError",
        asyncssh.SFTPError: "SFTPError",
        asyncssh.SFTPFailure: "SFTPFailure",
        asyncssh.SFTPConnectionLost: "SFTPConnectionLost",
        asyncssh.SFTPOpUnsupported: "SFTPOpUnsupported",
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
    if isinstance(error, SshConfigurationError):
        return ConnectionFailureCategory.configuration
    if isinstance(error, TailnetValidationError):
        return ConnectionFailureCategory.tailnet_validation
    if isinstance(error, asyncssh.HostKeyNotVerifiable):
        return ConnectionFailureCategory.host_key
    if isinstance(error, asyncssh.PermissionDenied):
        return ConnectionFailureCategory.authentication
    if isinstance(error, TimeoutError):
        return ConnectionFailureCategory.timeout
    if isinstance(error, OSError):
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
