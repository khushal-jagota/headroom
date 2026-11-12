"""Select one OS implementation without a fallback adapter."""

from __future__ import annotations

import platform

from planner.platform_runtime.contracts import (
    HomeRuntime,
    PlatformAdapter,
    UnsupportedPlatformError,
)
from planner.platform_runtime.targets import LINUX_HOME, RuntimeTarget, require_runtime_target


def get_platform_adapter(system: str | None = None) -> PlatformAdapter:
    selected = (platform.system() if system is None else system).lower()
    if selected != "linux":
        raise UnsupportedPlatformError(f"Platform runtime is not implemented for {selected}.")
    from planner.platform_runtime.linux.adapter import LinuxPlatformAdapter

    return LinuxPlatformAdapter()


def select_home_runtime(target: RuntimeTarget) -> HomeRuntime:
    """Select only the implemented Linux home runtime."""
    require_runtime_target(target, LINUX_HOME)
    from planner.platform_runtime.linux.runtime import LinuxRuntime

    return LinuxRuntime()
