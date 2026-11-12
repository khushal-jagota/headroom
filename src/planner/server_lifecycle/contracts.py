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


@dataclass(frozen=True)
class EventLoopTasksRequest:
    """Ask the application process to list the asyncio tasks on its main event loop."""

    version: int
    operation: Literal["list_event_loop_tasks"]


@dataclass(frozen=True)
class EventLoopTaskFrame:
    file: str
    line: int | None
    function: str


@dataclass(frozen=True)
class EventLoopTask:
    """One task on the application's event loop, as it stood when the answer was built.

    `stack` follows the task's await chain from its own coroutine, first, down to the
    innermost coroutine, last: the line where the task is waiting. `age_seconds` is the
    time since the task was created, or None when its creation was not recorded. For a
    task that already existed when recording began, it counts from then: a lower bound.
    """

    name: str
    coroutine: str
    age_seconds: float | None
    stack: tuple[EventLoopTaskFrame, ...]


@dataclass(frozen=True)
class EventLoopTasksResponse:
    """Every task on the loop, oldest first, minus the ones the size limit left out."""

    version: int
    total_task_count: int
    omitted_task_count: int
    tasks: tuple[EventLoopTask, ...]


class ServerLifecycleError(RuntimeError):
    """Base error for foreground server lifecycle operations."""


class ServerLifecycleAlreadyOwnedError(ServerLifecycleError):
    """The configured local port already has a foreground supervisor."""


class ServerRestartConnectionError(ServerLifecycleError):
    """The restart client could not reach the owning supervisor."""


class ServerRestartProtocolError(ServerLifecycleError):
    """The supervisor returned a malformed or incompatible control reply."""


class EventLoopTasksError(ServerLifecycleError):
    """The application's event loop tasks could not be read."""


class EventLoopTasksConnectionError(EventLoopTasksError):
    """No application answers on the event-loop-tasks socket."""


class EventLoopTasksTimeoutError(EventLoopTasksError):
    """The application accepted the request but did not answer in time."""


class EventLoopTasksProtocolError(EventLoopTasksError):
    """A request or answer was malformed, oversized, or incompatible."""


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
