"""Contracts for the local Panels server-lifecycle control protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

SERVER_CONTROL_PROTOCOL_VERSION: Final = 1


@dataclass(frozen=True)
class ServerControlRequest:
    version: int
    operation: Literal["restart"]


@dataclass(frozen=True)
class ServerControlResponse:
    version: int
    status: Literal["accepted"]


class ServerLifecycleError(RuntimeError):
    """Base error for foreground server lifecycle operations."""


class ServerLifecycleAlreadyOwnedError(ServerLifecycleError):
    """The configured local port already has a foreground supervisor."""


class ServerRestartConnectionError(ServerLifecycleError):
    """The restart client could not reach the owning supervisor."""


class ServerRestartProtocolError(ServerLifecycleError):
    """The supervisor returned a malformed or incompatible control reply."""
