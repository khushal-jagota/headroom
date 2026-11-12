"""Preserve the pre-adapter POSIX source-server path without native home features.

Socket addressing, flock, and child launch use the same portable operations that
Linux already uses. This is not a macOS home adapter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from planner.environments.vps_metrics.config import MetricsConfig
from planner.environments.vps_metrics.contracts import Collector, Sample
from planner.platform_runtime.contracts import PreviewProcessInspector, UnsupportedPlatformError
from planner.platform_runtime.linux.adapter import LinuxPlatformAdapter
from planner.previews.contracts import PreviewError
from planner.previews.contracts import ProcessIdentity as PreviewIdentity
from planner.server_lifecycle.contracts import ProcessIdentity, ShutdownPolicy


class UnavailablePreviewInspector:
    def discover(self, port: int, worktree: Path) -> PreviewIdentity:
        raise PreviewError("Preview process evidence is unavailable on this operating system.", 503)

    def check(self, identity: PreviewIdentity, *, deadline: float | None = None) -> None:
        raise PreviewError("Preview process evidence is unavailable on this operating system.", 503)

    def connected(self, identity: PreviewIdentity, local_port: int) -> None:
        raise PreviewError("Preview process evidence is unavailable on this operating system.", 503)


class UnavailableMetricsCollector:
    def collect(self, *, include_slow: bool) -> Sample:
        raise UnsupportedPlatformError(
            "Resource collection is unavailable on this operating system."
        )


class LegacyPosixPlatformAdapter(LinuxPlatformAdapter):
    """Retain portable lifecycle operations and refuse Linux-only capabilities."""

    update_unavailable_reason = "Home updates are unavailable on this operating system."

    def read_process_identity(self, pid: int) -> ProcessIdentity | None:
        return None

    def read_authenticated_manager_identity(
        self, pid: int, bus: Literal["user", "system"] | None = None
    ) -> ProcessIdentity | None:
        return None

    def revalidate_manager_identity(self, manager: ProcessIdentity) -> ProcessIdentity | None:
        return None

    def capture_shutdown_policy(self) -> ShutdownPolicy:
        return ShutdownPolicy("dogfood", os.getppid(), None)

    def process_is_zombie(self, pid: int) -> bool:
        return False

    def preview_inspector(self) -> PreviewProcessInspector:
        return UnavailablePreviewInspector()

    def metrics_collector(self, config: MetricsConfig) -> Collector:
        return UnavailableMetricsCollector()

    def own_service_unit(self, cgroup: str | None = None) -> str | None:
        raise UnsupportedPlatformError(self.update_unavailable_reason)

    def launch_installer(self, attempt_id: str, command: list[str]) -> None:
        raise UnsupportedPlatformError(self.update_unavailable_reason)

    def installer_source(self) -> Path:
        raise UnsupportedPlatformError(self.update_unavailable_reason)
