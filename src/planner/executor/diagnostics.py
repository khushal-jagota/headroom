"""Finite, fixed diagnostics at the existing connection retry boundary."""

import logging
from collections.abc import Callable

import asyncssh

from planner.executor.contracts import (
    ConnectionFailureCategory,
    SshConfigurationError,
    TailnetValidationError,
)


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

    def report(self, error: Exception) -> None:
        category = classify_connection_failure(error)
        if category not in self._seen:
            self._seen.add(category)
            self._emit(f"executor connection failure: {category.value}")


# One finite budget for the process, including any replaced executor instance.
connection_diagnostics = ConnectionDiagnostics(logging.getLogger(__name__).warning)
