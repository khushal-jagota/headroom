"""Platform-selected process identity operations for server lifecycle."""

from __future__ import annotations

from typing import Literal

from planner.platform_runtime.selection import get_platform_adapter
from planner.server_lifecycle.contracts import ProcessIdentity, ShutdownPolicy


def read_process_identity(pid: int) -> ProcessIdentity | None:
    return get_platform_adapter().read_process_identity(pid)


def read_authenticated_manager_identity(
    pid: int, bus: Literal["user", "system"] | None = None
) -> ProcessIdentity | None:
    return get_platform_adapter().read_authenticated_manager_identity(pid, bus)


def revalidate_manager_identity(manager: ProcessIdentity) -> ProcessIdentity | None:
    return get_platform_adapter().revalidate_manager_identity(manager)


def capture_shutdown_policy() -> ShutdownPolicy:
    return get_platform_adapter().capture_shutdown_policy()
