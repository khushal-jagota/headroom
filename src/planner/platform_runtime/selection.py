"""Select one OS implementation without a fallback adapter."""

from __future__ import annotations

import platform

from planner.platform_runtime.contracts import (
    HomeRuntime,
    PlatformAdapter,
    RuntimeRequirements,
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


def parse_optional_runtime_contract(value: object) -> RuntimeRequirements | None:
    """Legacy absence stays readable; malformed Linux data keeps its original errors."""
    if value is None:
        return None
    if isinstance(value, dict):
        system = value.get("system")
        if isinstance(system, str) and system != "linux":
            raise UnsupportedPlatformError(
                f"Home runtime contract is not implemented for {system}."
            )
    return select_home_runtime(LINUX_HOME).parse_contract(value)
