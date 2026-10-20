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


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: int
    executable_path: str | None
    executable_device: int | None
    executable_inode: int | None
    identity_source: Literal["proc_executable", "dbus_manager"] = "proc_executable"
    manager_bus: Literal["user", "system"] | None = None
    manager_uid: int | None = None


@dataclass(frozen=True)
class ShutdownPolicy:
    mode: Literal["dogfood", "guarded"]
    launch_parent_pid: int
    authorized_manager: ProcessIdentity | None


@dataclass(frozen=True)
class ShutdownDecision:
    accepted: bool
    reason: str
